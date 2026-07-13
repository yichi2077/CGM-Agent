"""Guarded default-Hermes activation and rollback helpers.

The acceptance runner never edits the installed Hermes package.  It may edit a
user profile only after every isolated hard gate has passed, and it keeps a
file-level rollback bundle beside the acceptance artifacts.  All subprocess
output is treated as untrusted and redacted before it is written to a report.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hermes_cgm_agent.services.acceptance.hermes import normalize_provider
from hermes_cgm_agent.services.acceptance.hermes import HermesClient
from hermes_cgm_agent.storage.sqlite import SQLiteStore


@dataclass
class DeliveryBudget:
    limit: int
    sent: int = 0

    def reserve(self) -> bool:
        if self.sent >= self.limit:
            return False
        self.sent += 1
        return True


@dataclass
class CutoverOutcome:
    requested: bool
    performed: bool = False
    rolled_back: bool = False
    backup_dir: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    external_messages: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "performed": self.performed,
            "rolled_back": self.rolled_back,
            "backup_dir": self.backup_dir,
            "steps": self.steps,
            "issues": self.issues,
            "external_messages": self.external_messages,
        }


class CutoverManager:
    def __init__(
        self,
        *,
        hermes_bin: Path,
        hermes_home: Path,
        source_db: Path,
        user_id: str,
        project_root: Path,
        provider: str,
        provider_user_agent: str | None = None,
        provider_max_tokens: int | None = None,
        model: str,
        output_dir: Path,
        run_id: str,
        deliver: str | None = None,
        max_external_messages: int = 6,
        send_external: bool = False,
        acceptance_dates: list[str] | None = None,
        acceptance_events: list[dict[str, Any]] | None = None,
        data_source: str | None = None,
    ) -> None:
        self.hermes_bin = hermes_bin
        self.hermes_home = hermes_home
        self.source_db = source_db
        self.user_id = user_id
        self.project_root = project_root
        self.provider = normalize_provider(provider)
        self.provider_user_agent = (provider_user_agent or "").strip() or None
        self.provider_max_tokens = provider_max_tokens
        self.model = model
        self.output_dir = output_dir
        self.run_id = run_id
        self.deliver = deliver
        self.budget = DeliveryBudget(max_external_messages)
        self.send_external = send_external
        # These values come from the deterministic periodic oracle.  They are
        # copied into one-shot prompts so a delivered acceptance message is
        # tied to a real simulated date/event rather than a generic heartbeat.
        self.acceptance_dates = [str(value) for value in (acceptance_dates or []) if str(value).strip()]
        self.acceptance_events = [
            dict(value) for value in (acceptance_events or []) if isinstance(value, dict)
        ]
        self.data_source = (data_source or "").strip() or None
        self.backup_dir = output_dir / "cutover-backup"

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HERMES_HOME": str(self.hermes_home),
                "CGM_AGENT_DB_PATH": str(self.source_db),
                "CGM_AGENT_STORAGE_KEY_PATH": str(self.source_db.parent / "storage.key"),
                "CGM_AGENT_USER_ID": self.user_id,
                "CGM_AGENT_ENFORCE_USER_ID": "1",
                "CGM_AGENT_PROJECT_ROOT": str(self.project_root),
                "PYTHONPATH": os.pathsep.join(
                    [str(self.project_root / "src"), env.get("PYTHONPATH", "")]
                ).strip(os.pathsep),
            }
        )
        if self.data_source:
            env["CGM_AGENT_ACCEPTANCE_SOURCE"] = self.data_source
            env["CGM_AGENT_ENFORCE_DATA_SOURCE"] = "1"
        return env

    def activate(self) -> CutoverOutcome:
        outcome = CutoverOutcome(requested=True, backup_dir=str(self.backup_dir))
        try:
            if not self.deliver:
                outcome.issues.append("no configured Weixin delivery target was found")
            else:
                self._backup(outcome)
                self._set_runtime_env(outcome)
                self._set_model_config(outcome)
                self._run_step(outcome, ["plugins", "enable", "cgm"], "enable_cgm_plugin")
                self._run_step(outcome, ["memory", "setup", "cgm_memory"], "enable_cgm_memory")
                if outcome.issues:
                    raise RuntimeError("profile setup failed before cron activation")
                self._install_scripts(outcome)
                self._pause_existing_cgm_jobs(outcome)
                if outcome.issues:
                    raise RuntimeError("existing CGM cron pause failed")
                if self.send_external:
                    # Three daily jobs plus up to two event jobs leave one
                    # slot for the post-cutover canary, keeping the global
                    # cap at six.  The jobs are run immediately and marked
                    # repeat=1 so the delivery path is exercised now rather
                    # than merely proving that a JSON record was created.
                    self.create_acceptance_jobs(outcome)
                    if outcome.issues:
                        raise RuntimeError("acceptance delivery job failed")
                self._create_normal_jobs(outcome)
                if outcome.issues:
                    raise RuntimeError("normal cron job creation failed")
                self._run_step(outcome, ["gateway", "restart"], "restart_gateway")
                if outcome.issues:
                    raise RuntimeError("gateway restart failed")
                self._verify_profile(outcome)
                if self.send_external and not outcome.issues:
                    self._send_canary(outcome)
            outcome.performed = not outcome.issues
        except Exception as exc:  # noqa: BLE001 - rollback must run for every failure
            outcome.issues.append(f"{type(exc).__name__}: {exc}")
        if not outcome.performed:
            self.rollback(outcome)
        return outcome

    def rollback(self, outcome: CutoverOutcome) -> None:
        if not self.backup_dir.exists():
            return
        try:
            self._run_step(outcome, ["gateway", "stop"], "rollback_stop_gateway", record_issue=False)
            for relative in ("config.yaml", ".env", "cron/jobs.json", "cgm-agent/app.db", "cgm-agent/storage.key"):
                source = self.backup_dir / relative
                if relative == "cgm-agent/app.db":
                    target = self.source_db
                elif relative == "cgm-agent/storage.key":
                    target = self.source_db.parent / "storage.key"
                else:
                    target = self.hermes_home / relative
                if source.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            manifest_path = self.backup_dir / "manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mismatches = []
                for entry in manifest.get("files", []):
                    relative = Path(str(entry.get("path") or ""))
                    if relative.as_posix() == "cgm-agent/app.db":
                        target = self.source_db
                    elif relative.as_posix() == "cgm-agent/storage.key":
                        target = self.source_db.parent / "storage.key"
                    else:
                        target = self.hermes_home / relative
                    if target.exists() and _sha256(target) != entry.get("sha256"):
                        mismatches.append(str(relative))
                if mismatches:
                    outcome.issues.append("rollback hash verification failed")
                outcome.steps.append({"name": "rollback_verify", "status": "ok" if not mismatches else "failed"})
            self._run_step(outcome, ["gateway", "restart"], "rollback_restart_gateway", record_issue=False)
            outcome.rolled_back = True
        except Exception as exc:  # noqa: BLE001
            outcome.issues.append(f"rollback {type(exc).__name__}: {exc}")

    def _backup(self, outcome: CutoverOutcome) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        paths = [
            self.hermes_home / "config.yaml",
            self.hermes_home / ".env",
            self.hermes_home / "cron" / "jobs.json",
            self.source_db,
            self.source_db.parent / "storage.key",
        ]
        manifest: dict[str, Any] = {"run_id": self.run_id, "files": []}
        for source in paths:
            if not source.exists():
                continue
            try:
                relative = source.relative_to(self.hermes_home)
            except ValueError:
                relative = Path("cgm-agent") / source.name
            target = self.backup_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source == self.source_db:
                _copy_sqlite(source, target)
            else:
                shutil.copy2(source, target)
            manifest["files"].append(
                {"path": str(relative), "sha256": _sha256(target), "size": target.stat().st_size}
            )
        (self.backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        outcome.steps.append({"name": "backup", "status": "ok", "file_count": len(manifest["files"])})

    def _set_runtime_env(self, outcome: CutoverOutcome) -> None:
        path = self.hermes_home / ".env"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        values = {
            "CGM_AGENT_USER_ID": self.user_id,
            "CGM_AGENT_ENFORCE_USER_ID": "1",
            "CGM_AGENT_DB_PATH": str(self.source_db),
            "CGM_AGENT_STORAGE_KEY_PATH": str(self.source_db.parent / "storage.key"),
            "CGM_AGENT_PROJECT_ROOT": str(self.project_root),
            "CGM_AGENT_PROVIDER": self.provider,
            "CGM_AGENT_MODEL": self.model,
        }
        if self.data_source:
            values["CGM_AGENT_ACCEPTANCE_SOURCE"] = self.data_source
            values["CGM_AGENT_ENFORCE_DATA_SOURCE"] = "1"
        if self.provider_user_agent:
            values["CGM_AGENT_PROVIDER_USER_AGENT"] = self.provider_user_agent
        lines = existing.splitlines()
        seen: set[str] = set()
        updated: list[str] = []
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if key in values:
                updated.append(f"{key}={values[key]}")
                seen.add(key)
            else:
                updated.append(line)
        updated.extend(f"{key}={value}" for key, value in values.items() if key not in seen)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(updated) + "\n", encoding="utf-8")
        outcome.steps.append({"name": "set_runtime_env", "status": "ok", "keys": sorted(values)})

    def _set_model_config(self, outcome: CutoverOutcome) -> None:
        """Point the user profile at the explicitly accepted custom model."""

        path = self.hermes_home / "config.yaml"
        if not path.exists():
            outcome.issues.append("default Hermes config.yaml is missing")
            return
        try:
            import yaml

            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            model_config = config.setdefault("model", {})
            if not isinstance(model_config, dict):
                raise ValueError("model config is not a mapping")
            model_config["provider"] = self.provider
            model_config["default"] = self.model
            if self.provider_user_agent:
                headers = model_config.setdefault("default_headers", {})
                if not isinstance(headers, dict):
                    raise ValueError("model.default_headers is not a mapping")
                headers["User-Agent"] = self.provider_user_agent
            if self.provider_max_tokens is not None:
                if isinstance(self.provider_max_tokens, bool) or self.provider_max_tokens <= 0:
                    raise ValueError("provider_max_tokens must be a positive integer")
                providers = config.get("custom_providers")
                if not isinstance(providers, list):
                    raise ValueError("custom_providers must be a list")
                updated = 0
                for entry in providers:
                    if not isinstance(entry, dict):
                        continue
                    identity = str(entry.get("provider_key") or entry.get("name") or "")
                    if normalize_provider("custom:" + identity) != self.provider:
                        continue
                    extra_body = entry.setdefault("extra_body", {})
                    if not isinstance(extra_body, dict):
                        raise ValueError("custom provider extra_body is not a mapping")
                    extra_body["max_tokens"] = self.provider_max_tokens
                    updated += 1
                if not updated:
                    raise ValueError("no custom provider entry found for max_tokens override")
            entries = config.get("custom_providers")
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    identity = str(entry.get("provider_key") or entry.get("name") or "")
                    if normalize_provider("custom:" + identity) == self.provider:
                        entry["model"] = self.model
            path.write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            outcome.issues.append(f"model config update failed: {type(exc).__name__}")
            return
        outcome.steps.append(
            {"name": "set_model_config", "status": "ok", "provider": self.provider, "model": self.model}
        )

    def _install_scripts(self, outcome: CutoverOutcome) -> None:
        source_dir = self.project_root / "scripts" / "hermes_cron"
        target_dir = self.hermes_home / "scripts"
        target_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for source in source_dir.glob("*.py"):
            target = target_dir / source.name
            shutil.copy2(source, target)
            copied.append(source.name)
        outcome.steps.append({"name": "install_cron_scripts", "status": "ok", "scripts": copied})

    def _pause_existing_cgm_jobs(self, outcome: CutoverOutcome) -> None:
        jobs_path = self.hermes_home / "cron" / "jobs.json"
        if not jobs_path.exists():
            outcome.steps.append({"name": "pause_old_cgm_jobs", "status": "ok", "paused": []})
            return
        payload = json.loads(jobs_path.read_text(encoding="utf-8"))
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        paused: list[str] = []
        for job in jobs:
            if not isinstance(job, dict) or not job.get("enabled", True):
                continue
            name = str(job.get("name") or "")
            if not name.lower().startswith("cgm"):
                continue
            job_id = str(job.get("id") or "")
            step_name = f"pause:{name}"
            if job_id:
                self._run_step(outcome, ["cron", "pause", job_id], step_name)
            if job_id and self._step_ok(outcome, step_name):
                paused.append(name)
        outcome.steps.append({"name": "pause_old_cgm_jobs", "status": "ok", "paused": paused})

    def _create_normal_jobs(self, outcome: CutoverOutcome) -> None:
        daily_prompt = (
            "运行 CGM 每日个人陪伴总结，调用报告和事实工具；只使用当前用户数据，"
            "回答使用中文生活语言；只有在有新的到期事实时才投递消息。"
        )
        commands = [
            (
                [
                    "cron",
                    "create",
                    "0 9 * * *",
                    daily_prompt,
                    "--name",
                    f"cgm-normal-daily-{self.run_id}",
                    "--deliver",
                    self.deliver or "origin",
                ],
                "create_normal_daily",
            ),
            (
                [
                    "cron",
                    "create",
                    "every 30m",
                    "--name",
                    f"cgm-normal-events-{self.run_id}",
                    "--script",
                    "cgm_event_monitor.py",
                    "--no-agent",
                    "--deliver",
                    self.deliver or "origin",
                ],
                "create_normal_event_monitor",
            ),
            (
                [
                    "cron",
                    "create",
                    "every 2h",
                    "--name",
                    f"cgm-normal-health-{self.run_id}",
                    "--script",
                    "cgm_health_check.py",
                    "--no-agent",
                    "--deliver",
                    self.deliver or "origin",
                ],
                "create_normal_health",
            ),
        ]
        for command, name in commands:
            self._run_step(outcome, command, name)

    def create_acceptance_jobs(self, outcome: CutoverOutcome, *, event_count: int | None = None) -> None:
        """Create capped, one-shot acceptance jobs after isolated hard gates pass."""

        if not self.send_external or not self.deliver:
            return
        if len(self.acceptance_dates) != 3:
            outcome.issues.append("acceptance jobs require exactly three oracle-backed simulated dates")
            return
        available_events = self.acceptance_events[:3]
        if event_count is not None:
            available_events = available_events[: max(0, event_count)]
        # Reserve the canary slot before creating event jobs.  At most two
        # event messages are therefore attempted when --send-external is on.
        event_limit = min(len(available_events), 2)
        for index, simulated_date in enumerate(self.acceptance_dates):
            if not self.budget.reserve():
                outcome.issues.append("external delivery budget exhausted while creating acceptance jobs")
                break
            correlation_id = f"{self.run_id}:daily:{simulated_date}"
            prompt = (
                f"[CGM模拟验收] run_id={self.run_id} correlation_id={correlation_id} "
                f"模拟日期={simulated_date}。请只根据该模拟日期的 CGM 数据生成简短中文日总结，"
                "回答首行必须保留上述前缀；不得伪装成真实健康提醒，不得引用其它日期或内部标签。"
            )
            self._run_step(
                outcome,
                [
                    "cron",
                    "create",
                    f"every {index + 1}m",
                    prompt,
                    "--name",
                    f"hermes-accept-{self.run_id}-daily-{index + 1}",
                    "--deliver",
                    self.deliver,
                    "--repeat",
                    "1",
                ],
                f"create_acceptance_job:daily:{index + 1}",
            )
            if self._step_ok(outcome, f"create_acceptance_job:daily:{index + 1}"):
                job_id = self._job_id_by_name(f"hermes-accept-{self.run_id}-daily-{index + 1}")
                if not job_id:
                    outcome.issues.append(f"acceptance daily job {index + 1} was not persisted")
                else:
                    outcome.steps.append(
                        {
                            "name": "acceptance_link",
                            "status": "ok",
                            "job_id": job_id,
                            "correlation_id": correlation_id,
                        }
                    )
                    run_output = self._run_step(
                        outcome,
                        ["cron", "run", job_id],
                        f"run_acceptance_job:daily:{index + 1}",
                    )
                    self._check_job_execution(
                        outcome,
                        job_id,
                        run_output,
                        f"run_acceptance_job:daily:{index + 1}",
                        correlation_id=correlation_id,
                    )
        for index, event in enumerate(available_events[:event_limit], start=1):
            if not self.budget.reserve():
                outcome.issues.append("external delivery budget exhausted while creating event jobs")
                break
            simulated_date = str(event.get("date") or "unknown-date")
            event_id = str(event.get("event_id") or "unknown-event")
            event_type = str(event.get("event_type") or "event")
            correlation_id = f"{self.run_id}:event:{event_id}"
            prompt = (
                f"[CGM模拟验收] run_id={self.run_id} correlation_id={correlation_id} "
                f"模拟日期={simulated_date} event_id={event_id} event_type={event_type}。"
                "只核对该 oracle 已确认的模拟事件并给出不超过 120 字的中文提醒，"
                "回答首行必须保留上述前缀；不得伪装成真实健康提醒。"
            )
            job_name = f"hermes-accept-{self.run_id}-event-{index}"
            self._run_step(
                outcome,
                [
                    "cron",
                    "create",
                    f"every {index + 4}m",
                    prompt,
                    "--name",
                    job_name,
                    "--deliver",
                    self.deliver,
                    "--repeat",
                    "1",
                ],
                f"create_acceptance_job:event:{index}",
            )
            if self._step_ok(outcome, f"create_acceptance_job:event:{index}"):
                job_id = self._job_id_by_name(job_name)
                if not job_id:
                    outcome.issues.append(f"acceptance event job {index} was not persisted")
                else:
                    outcome.steps.append(
                        {
                            "name": "acceptance_link",
                            "status": "ok",
                            "job_id": job_id,
                            "correlation_id": correlation_id,
                        }
                    )
                    run_output = self._run_step(
                        outcome,
                        ["cron", "run", job_id],
                        f"run_acceptance_job:event:{index}",
                    )
                    self._check_job_execution(
                        outcome,
                        job_id,
                        run_output,
                        f"run_acceptance_job:event:{index}",
                        correlation_id=correlation_id,
                    )
        outcome.external_messages = self.budget.sent

    def _job_id_by_name(self, name: str) -> str | None:
        path = self.hermes_home / "cron" / "jobs.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        for job in payload.get("jobs", []) if isinstance(payload, dict) else []:
            if isinstance(job, dict) and str(job.get("name") or "") == name:
                return str(job.get("id") or "") or None
        return None

    def _check_job_execution(
        self,
        outcome: CutoverOutcome,
        job_id: str,
        output: str,
        step_name: str,
        correlation_id: str | None = None,
    ) -> None:
        lowered = output.lower()
        # Hermes can report the cron action itself as successful while the
        # live adapter rejects the delivery.  Treat those messages as a hard
        # gate even when a repeat=1 job has already been removed from
        # jobs.json, otherwise a rate-limited Weixin send would look green.
        delivery_failure_markers = (
            "delivery failed",
            "delivery error",
            "send failed",
            "sendmessage rate limited",
            "cooldown active",
        )
        if "ran now: failed" in lowered:
            outcome.issues.append(f"{step_name} reported execution failure")
            return
        if any(marker in lowered for marker in delivery_failure_markers):
            outcome.issues.append(f"{step_name} reported delivery failure")
            return
        path = self.hermes_home / "cron" / "jobs.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            payload = {}
        for job in payload.get("jobs", []) if isinstance(payload, dict) else []:
            if not isinstance(job, dict) or str(job.get("id") or "") != job_id:
                continue
            # repeat=1 jobs are removed after a successful run; if they are
            # still present, require an explicit successful state and no
            # delivery error.  A removed job is the normal success path.
            if job.get("last_status") != "ok" or job.get("last_delivery_error"):
                outcome.issues.append(f"{step_name} did not record a successful delivery")
            return
        if "ran now: succeeded" not in lowered:
            outcome.issues.append(f"{step_name} did not confirm execution")
        if "ran now: succeeded" in lowered:
            self._record_acceptance_links(outcome, job_id, correlation_id)

    def _record_acceptance_links(
        self,
        outcome: CutoverOutcome,
        job_id: str,
        correlation_id: str | None,
    ) -> None:
        """Capture the cron -> Hermes session -> CGM audit correlation.

        Hermes uses ``cron_<job_id>_<timestamp>`` session ids.  Reading the
        project audit table after ``cron run`` keeps this sidecar association
        outside production business tables while still proving that the
        delivered job reached the CGM tool/audit boundary.
        """

        if not self.source_db.exists():
            return
        session_prefix = f"cron_{job_id}_%"
        session_ids: set[str] = set()
        audit_ids: set[str] = set()
        push_ids: set[str] = set()
        try:
            store = SQLiteStore(self.source_db)
            with store.connect() as conn:
                rows = conn.execute(
                    "SELECT id, session_id, payload_json FROM audit_logs "
                    "WHERE session_id LIKE ? ORDER BY created_at",
                    (session_prefix,),
                ).fetchall()
            for row in rows:
                session_id = str(row["session_id"] or "")
                if session_id:
                    session_ids.add(session_id)
                audit_id = str(row["id"] or "")
                if audit_id:
                    audit_ids.add(audit_id)
                try:
                    payload = store.unseal(row["payload_json"], legacy="json") or {}
                except Exception:
                    payload = {}
                if isinstance(payload, dict):
                    for key in ("push_id", "delivery_id"):
                        value = str(payload.get(key) or "")
                        if value:
                            push_ids.add(value)
        except Exception as exc:  # noqa: BLE001 - correlation is a hard gate
            outcome.issues.append(f"acceptance correlation lookup failed: {type(exc).__name__}")
            return
        outcome.steps.append(
            {
                "name": "acceptance_correlation",
                "status": "ok" if session_ids and audit_ids else "failed",
                "job_id": job_id,
                "correlation_id": correlation_id,
                "session_ids": sorted(session_ids),
                "audit_ids": sorted(audit_ids),
                "push_ids": sorted(push_ids),
            }
        )
        if not session_ids:
            outcome.issues.append(f"acceptance job {job_id} produced no Hermes session audit")
        elif not audit_ids:
            outcome.issues.append(f"acceptance job {job_id} produced no CGM tool audit")

    def _verify_profile(self, outcome: CutoverOutcome) -> None:
        memory = self._run_step(outcome, ["memory", "status"], "verify_memory_provider")
        plugins = self._run_step(outcome, ["plugins", "list", "--enabled", "--plain"], "verify_plugins")
        tools = self._run_step(outcome, ["tools", "list", "--platform", "cli"], "verify_cgm_tools")
        if "cgm_memory" not in memory.lower():
            outcome.issues.append("memory status did not report cgm_memory")
        if "cgm" not in plugins.lower():
            outcome.issues.append("enabled plugin list did not report cgm")
        if "cgm" not in tools.lower():
            outcome.issues.append("Hermes tool listing did not report cgm")
        try:
            import yaml

            config = yaml.safe_load((self.hermes_home / "config.yaml").read_text(encoding="utf-8")) or {}
            model_config = config.get("model") or {}
            if model_config.get("provider") != self.provider or model_config.get("default") != self.model:
                outcome.issues.append("default model/provider does not match the accepted custom provider")
        except Exception as exc:  # noqa: BLE001
            outcome.issues.append(f"profile config verification failed: {type(exc).__name__}")
        if not self.source_db.exists() or not (self.source_db.parent / "storage.key").exists():
            outcome.issues.append("default CGM database or storage key is missing")
        client = HermesClient(
            executable=self.hermes_bin,
            hermes_home=self.hermes_home,
            db_path=self.source_db,
            project_root=self.project_root,
            user_id=self.user_id,
            provider=self.provider,
            model=self.model,
            data_source=self.data_source,
            timeout_seconds=180,
        )
        conversation = client.run("请用一句中文确认默认 CGM profile 对话已连通。", scenario_id="default-profile")
        outcome.steps.append(
            {
                "name": "verify_default_conversation",
                "status": "ok" if conversation.exit_code == 0 and conversation.response.strip() else "failed",
                "exit_code": conversation.exit_code,
            }
        )
        if conversation.exit_code != 0 or not conversation.response.strip():
            outcome.issues.append("default profile conversation failed")

    def _send_canary(self, outcome: CutoverOutcome) -> None:
        if not self.deliver:
            outcome.issues.append("external delivery requested without --deliver target")
            return
        if not self.budget.reserve():
            outcome.issues.append("external delivery budget exhausted")
            return
        simulated_date = self.acceptance_dates[-1] if self.acceptance_dates else "unknown-date"
        correlation_id = f"{self.run_id}:canary:{simulated_date}"
        message = (
            f"[CGM模拟验收] run_id={self.run_id} correlation_id={correlation_id} "
            f"模拟日期={simulated_date} 默认 profile canary：模拟数据链路已通过。"
        )
        self._run_step(
            outcome,
            ["send", "--to", self.deliver, "--quiet", message],
            "send_weixin_canary",
        )
        outcome.steps.append(
            {
                "name": "canary_correlation",
                "status": "ok" if self._step_ok(outcome, "send_weixin_canary") else "failed",
                "correlation_id": correlation_id,
                "simulated_date": simulated_date,
            }
        )
        if self._step_ok(outcome, "send_weixin_canary"):
            outcome.external_messages = self.budget.sent

    @staticmethod
    def _step_ok(outcome: CutoverOutcome, name: str) -> bool:
        for step in reversed(outcome.steps):
            if step.get("name") == name:
                return step.get("status") == "ok"
        return False

    def _run_step(
        self,
        outcome: CutoverOutcome,
        args: list[str],
        name: str,
        *,
        record_issue: bool = True,
    ) -> str:
        try:
            completed = subprocess.run(
                [str(self.hermes_bin), *args],
                env=self.environment(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
            output = _safe_output((completed.stdout or "") + "\n" + (completed.stderr or ""))
            ok = completed.returncode == 0
            outcome.steps.append({"name": name, "status": "ok" if ok else "failed", "exit_code": completed.returncode})
            if not ok and record_issue:
                outcome.issues.append(f"{name} failed: {output[-400:]}")
            return output if ok else ""
        except Exception as exc:  # noqa: BLE001
            if record_issue:
                outcome.issues.append(f"{name} {type(exc).__name__}: {exc}")
            outcome.steps.append({"name": name, "status": "failed", "exit_code": 127})
            return ""


def _copy_sqlite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    src = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_output(text: str) -> str:
    import re

    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    return text.strip()
