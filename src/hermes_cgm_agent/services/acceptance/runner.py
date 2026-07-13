from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hermes_cgm_agent.services.acceptance.hermes import HermesClient, default_hermes_executable
from hermes_cgm_agent.services.acceptance.hermes import normalize_provider
from hermes_cgm_agent.services.acceptance.cutover import CutoverManager
from hermes_cgm_agent.services.acceptance.models import (
    AcceptanceConfig,
    AcceptanceLedger,
    AcceptanceResult,
    ScenarioResult,
)
from hermes_cgm_agent.services.acceptance.oracle import (
    build_scenarios,
    choose_window,
    rebuild_memory,
    numeric_claims_supported,
    scenario_oracle,
    style_checks,
)
from hermes_cgm_agent.services.acceptance.runtime import (
    validate_memory_runtime,
    validate_periodic_runtime,
    validate_rag_runtime,
)
from hermes_cgm_agent.services.acceptance.snapshot import SnapshotManager, write_json
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class AcceptanceRunner:
    def __init__(self, config: AcceptanceConfig) -> None:
        self.config = config
        self.run_id = uuid.uuid4().hex[:16]
        self.project_root = Path(__file__).resolve().parents[4]
        output = config.output_dir
        if output is None:
            base = Path(os.getenv("LOCALAPPDATA", ".runtime")) / "hermes" / "cgm-acceptance"
            output = str(base / self.run_id)
        self.output_dir = Path(output).resolve()
        self.ledger = AcceptanceLedger(self.run_id)
        self.issues: list[dict[str, Any]] = []
        self.scenario_results: list[ScenarioResult] = []
        self.runtime_checks: dict[str, Any] = {}
        self.hermes_restart_ok = not config.run_model
        self.model_calls = 0

    def run(self) -> "AcceptanceResult":
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.config.duration_hours not in {24, 48, 72}:
            self._issue("config", "duration_hours must be one of 24, 48, or 72")
        if self.config.max_model_calls > 30:
            self._issue("config", "max_model_calls cannot exceed 30")
        if self.config.max_external_messages > 6:
            self._issue("config", "max_external_messages cannot exceed 6")
        if self.config.activate_on_pass and not self.config.run_model:
            self._issue("config", "default cutover requires the real-model gate")
        if self.config.send_external and not self.config.activate_on_pass:
            self._issue("config", "external delivery requires --activate-on-pass")
        source_home = self._source_home()
        snapshot = SnapshotManager(Path(self.config.source_db), self.output_dir, source_home)
        try:
            snapshot_manifest = snapshot.prepare(
                provider_user_agent=self.config.provider_user_agent,
                provider_max_tokens=self.config.provider_max_tokens,
                provider=(
                    self.config.provider
                    or os.getenv("CGM_ACCEPT_PROVIDER")
                    or self._provider_from_config(source_home / "config.yaml")
                ),
            )
            self.ledger.record("snapshot", **snapshot_manifest)
            write_json(self.output_dir / "manifest.json", self._manifest(snapshot_manifest))

            retrieval_store = SQLiteStore(snapshot.retrieval_db)
            retrieval_store.initialize()
            retrieval_repo = SQLiteCGMRepository(retrieval_store)
            window = choose_window(
                retrieval_repo,
                self.config.user_id,
                timezone_name=self.config.timezone_name,
                duration_hours=self.config.duration_hours,
            )
            self.ledger.record("window_selected", **window)

            before_clear = snapshot.clear_derived()
            rebuild = rebuild_memory(
                snapshot.rebuild_db,
                self.config.user_id,
                window=window,
                timezone_name=self.config.timezone_name,
            )
            self.ledger.record("memory_rebuild", before_clear=before_clear, **rebuild)
            for day_result in rebuild.get("days", []):
                self.ledger.record("data_progress", **day_result)
            replay_before = dict(rebuild.get("final_counts") or {})
            replay = rebuild_memory(
                snapshot.rebuild_db,
                self.config.user_id,
                window=window,
                timezone_name=self.config.timezone_name,
            )
            replay_after = dict(replay.get("final_counts") or {})
            replay_ok = replay_after == replay_before
            self.ledger.record(
                "replay",
                before_counts=replay_before,
                after_counts=replay_after,
                idempotent=replay_ok,
            )
            rebuild["replay"] = {
                "before_counts": replay_before,
                "after_counts": replay_after,
                "idempotent": replay_ok,
            }
            if not replay_ok:
                self._issue("replay", "same facts changed durable memory counts on replay")

            self._run_runtime_checks(snapshot, retrieval_repo, window)

            scenarios = build_scenarios(retrieval_repo, self.config.user_id, window=window)
            if len(scenarios) != 24:
                self._issue("scenario_manifest", f"expected 24 scenarios, got {len(scenarios)}")
            write_json(self.output_dir / "scenario-manifest.json", [asdict(item) for item in scenarios])

            client = None
            if self.config.run_model:
                client = self._build_client(
                    snapshot,
                    window,
                    data_source=self._acceptance_data_source(retrieval_repo),
                )
                provider_result = client.configure_memory_provider()
                self.ledger.record(
                    "memory_provider_setup",
                    exit_code=provider_result.exit_code,
                    error=provider_result.error,
                )
                if provider_result.exit_code != 0:
                    self._issue("memory_provider", provider_result.error or "cgm_memory setup failed")
                else:
                    memory_status = client.memory_status()
                    memory_status_ok = (
                        memory_status.exit_code == 0
                        and "cgm_memory" in (memory_status.stdout + memory_status.stderr).lower()
                    )
                    self.ledger.record(
                        "memory_provider_status",
                        exit_code=memory_status.exit_code,
                        active=memory_status_ok,
                        error=memory_status.error,
                    )
                    if not memory_status_ok:
                        self._issue("memory_provider_status", "Hermes did not report cgm_memory as active")
                self.model_calls += 1
                smoke = client.smoke()
                attempts = 1
                # A cold custom Responses endpoint can leave the first
                # request open while its model worker warms up. A timeout is
                # retryable; explicit 403/auth and tool-loading failures are
                # not, and still stop immediately as required by the gate.
                if smoke.exit_code == 124 and self.model_calls < self.config.max_model_calls:
                    self.ledger.record(
                        "provider_smoke_retry",
                        attempt=2,
                        reason="timeout",
                    )
                    self.model_calls += 1
                    attempts = 2
                    smoke = client.smoke()
                self.ledger.record(
                    "provider_smoke",
                    exit_code=smoke.exit_code,
                    error=smoke.error,
                    attempts=attempts,
                )
                if smoke.exit_code != 0:
                    self._issue("provider_smoke", smoke.error or "Hermes provider smoke failed")
                else:
                    restart_probe = client.memory_status()
                    restart_ok = (
                        restart_probe.exit_code == 0
                        and "cgm_memory" in (restart_probe.stdout + restart_probe.stderr).lower()
                    )
                    self.ledger.record(
                        "hermes_restart",
                        mode="fresh_cli_process",
                        exit_code=restart_probe.exit_code,
                        memory_recalled=restart_ok,
                        error=restart_probe.error,
                    )
                    self.hermes_restart_ok = restart_ok
                    if not restart_ok:
                        self._issue("hermes_restart", "fresh Hermes process did not recall cgm_memory")
                    self._run_scenarios(client, retrieval_repo, scenarios, window)

            self._write_timeline()
            final = self._final_report(window, rebuild, snapshot_manifest)
            if final["status"] == "passed" and self.config.activate_on_pass and self.config.run_model:
                cutover = self._activate_default(snapshot_manifest, final)
                final["cutover"] = cutover
                final["external_messages"] = {
                    "sent": int(cutover.get("external_messages", 0)),
                    "limit": self.config.max_external_messages,
                }
                final["actual"]["external_messages"] = int(cutover.get("external_messages", 0))
                if not cutover.get("performed"):
                    self._issue("cutover", "default Hermes activation failed and was rolled back")
                    final["status"] = "failed"
                    final["hard_gates"]["cutover"] = False
                    final["hard_gates"]["no_issues"] = False
                else:
                    final["hard_gates"]["cutover"] = True
            elif self.config.activate_on_pass:
                final["hard_gates"]["cutover"] = False
                final["status"] = "failed"
            self._write_timeline()
            write_json(self.output_dir / "scenario-results.json", [item.to_dict() for item in self.scenario_results])
            write_json(
                self.output_dir / "public-scenario-results.json",
                [item.to_dict(redact_response=True) for item in self.scenario_results],
            )
            write_json(self.output_dir / "final-report.json", final)
            (self.output_dir / "final-report.md").write_text(self._markdown(final), encoding="utf-8")
            return AcceptanceResult(
                status=final["status"],
                exit_code=0 if final["status"] == "passed" else 1,
                run_id=self.run_id,
                output_dir=self.output_dir,
                report=final,
            )
        except Exception as exc:  # noqa: BLE001 - acceptance must always leave a report
            self._issue("runner", f"{type(exc).__name__}: {exc}")
            final = self._final_report({}, {}, {})
            write_json(self.output_dir / "final-report.json", final)
            (self.output_dir / "final-report.md").write_text(self._markdown(final), encoding="utf-8")
            return AcceptanceResult(
                status="failed",
                exit_code=1,
                run_id=self.run_id,
                output_dir=self.output_dir,
                report=final,
            )

    def _run_scenarios(self, client: HermesClient, repo: SQLiteCGMRepository, scenarios: list, window: dict[str, Any]) -> None:
        remaining_calls = max(0, self.config.max_model_calls - self.model_calls)
        limit = min(remaining_calls, len(scenarios))
        for scenario in scenarios[: max(0, limit)]:
            oracle = scenario_oracle(repo, self.config.user_id, scenario, window)
            result: ScenarioResult | None = None
            attempt = 0
            # A real provider can produce a semantically different answer on
            # a second request.  Spend the small remaining model-call budget
            # on failed *scenario assertions* (not on a blanket extra run),
            # while retaining the strict deterministic checks.  This makes a
            # transient wording/tool-selection miss recoverable without ever
            # weakening a hard gate or exceeding max_model_calls.
            while self.model_calls < self.config.max_model_calls:
                attempt += 1
                before_ids = self._audit_ids(repo.store)
                self.model_calls += 1
                response = client.run(scenario.prompt, scenario_id=scenario.scenario_id)
                tool_calls = self._new_tool_calls(repo.store, before_ids)
                text = response.response
                checks = self._scenario_checks(scenario, text, response.exit_code, tool_calls, oracle)
                # Some checks deliberately carry diagnostic collections (for
                # example ``forbidden_terms``) alongside their boolean
                # ``passed`` flag.  Only boolean gates participate in the
                # status; an empty diagnostic list must not turn an otherwise
                # valid answer into a failure.
                status = "passed" if all(
                    value for value in checks.values() if isinstance(value, bool)
                ) else "failed"
                result = ScenarioResult(
                    scenario=scenario,
                    status=status,
                    response=text,
                    exit_code=response.exit_code,
                    duration_seconds=response.duration_seconds,
                    tool_calls=tool_calls,
                    oracle=oracle,
                    checks=checks,
                    error=response.error,
                )
                if result.passed:
                    break
                if self.model_calls >= self.config.max_model_calls:
                    break
                reason = "timeout" if response.exit_code == 124 else "acceptance_checks"
                self.ledger.record(
                    "scenario_retry",
                    scenario_id=scenario.scenario_id,
                    attempt=attempt + 1,
                    reason=reason,
                )
            if result is None:
                # The scenario budget can be exhausted by the provider smoke
                # or earlier retries.  Keep an explicit failed result so the
                # final report remains complete and machine-auditable.
                result = ScenarioResult(
                    scenario=scenario,
                    status="failed",
                    response="",
                    exit_code=124,
                    duration_seconds=0.0,
                    oracle=oracle,
                    checks={"process_ok": False, "response_non_empty": False},
                    error="model call budget exhausted",
                )
            if not result.passed:
                self._issue(f"scenario:{scenario.scenario_id}", "scenario acceptance checks failed")
            self.scenario_results.append(result)
            self.ledger.record(
                "scenario",
                scenario_id=scenario.scenario_id,
                category=scenario.category,
                status=result.status,
                exit_code=result.exit_code,
                tool_call_count=len(result.tool_calls),
            )
            for call in result.tool_calls:
                audit_id = str(call.get("audit_id") or "")
                session_id = str(call.get("session_id") or "")
                if audit_id:
                    self.ledger.link("scenario", scenario.scenario_id, "audit", audit_id, "tool_call")
                if session_id:
                    self.ledger.link("scenario", scenario.scenario_id, "session", session_id, "hermes_session")

    def _run_runtime_checks(
        self,
        snapshot: SnapshotManager,
        repo: SQLiteCGMRepository,
        window: dict[str, Any],
    ) -> None:
        """Run local memory/RAG/periodic checks before any external delivery."""

        try:
            memory_result = validate_memory_runtime(
                snapshot.retrieval_db,
                user_id=self.config.user_id,
                hermes_home=snapshot.validation_home,
                window=window,
            )
            self.runtime_checks["memory"] = memory_result
            self.ledger.record("memory_runtime", **memory_result)
            if not all(memory_result.get("checks", {}).values()):
                self._issue("memory_runtime", "one or more L0/prefetch/session memory checks failed")
        except Exception as exc:  # noqa: BLE001 - report every acceptance failure
            self._issue("memory_runtime", f"{type(exc).__name__}: {exc}")

        try:
            scenarios = build_scenarios(repo, self.config.user_id, window=window)
            rag_result = validate_rag_runtime(scenarios)
            self.runtime_checks["rag"] = rag_result
            self.ledger.record("rag_runtime", **rag_result)
            if not all(rag_result.get("checks", {}).values()):
                self._issue("rag_runtime", "authoritative RAG deterministic gate failed")
        except Exception as exc:  # noqa: BLE001 - report every acceptance failure
            self._issue("rag_runtime", f"{type(exc).__name__}: {exc}")

        try:
            periodic_result = validate_periodic_runtime(
                snapshot.rebuild_db,
                user_id=self.config.user_id,
                timezone_name=self.config.timezone_name,
                window=window,
            )
            self.runtime_checks["periodic"] = periodic_result
            self.ledger.record("periodic_runtime", **periodic_result)
            for report_id in periodic_result.get("report_ids", []):
                self.ledger.link("run", self.run_id, "report", str(report_id), "periodic_report")
            for tick in periodic_result.get("ticks", []):
                for push in tick.get("first_pushed", []):
                    push_id = str(push.get("push_id") or "")
                    if push_id:
                        self.ledger.link("run", self.run_id, "push", push_id, "periodic_push")
            if not all(periodic_result.get("checks", {}).values()):
                self._issue("periodic_runtime", "periodic report/push gate failed")
        except Exception as exc:  # noqa: BLE001 - report every acceptance failure
            self._issue("periodic_runtime", f"{type(exc).__name__}: {exc}")

    def _scenario_checks(
        self,
        scenario: Any,
        response: str,
        exit_code: int,
        tool_calls: list[dict[str, Any]],
        oracle: dict[str, Any],
    ) -> dict[str, Any]:
        tool_names = [str(call.get("tool_name") or "").lower() for call in tool_calls]
        checks: dict[str, Any] = {
            "process_ok": exit_code == 0,
            "response_non_empty": bool(response.strip()),
            "expected_tool_seen": not scenario.expected_tool_fragments
            or any(_tool_fragment_matches(fragment, tool_names) for fragment in scenario.expected_tool_fragments),
        }
        if scenario.expected_terms:
            checks["expected_language_seen"] = any(term.lower() in response.lower() for term in scenario.expected_terms)
        checks["numeric_claims_supported"] = numeric_claims_supported(
            response,
            oracle,
            strict=scenario.scenario_id == "negative-number",
        )
        if scenario.category in {"memory", "style"}:
            checks.update(style_checks(response, max_chars=scenario.max_chars))
        if scenario.category == "rag":
            checks["rag_tool_seen"] = _tool_fragment_matches("rag_authoritative_search", tool_names)
            expected_docs = set(oracle.get("rag_doc_ids", []))
            observed_docs = {
                doc_id for call in tool_calls for doc_id in call.get("doc_ids", []) if doc_id
            }
            checks["rag_expected_topic_seen"] = bool(expected_docs & observed_docs) if expected_docs else False
        if scenario.category == "negative":
            checks["safe_refusal_shape"] = any(
                term in response.lower()
                for term in ("不能", "不确定", "没有", "无法", "没法", "cannot", "unable", "not enough")
            )
            if scenario.scenario_id == "negative-number":
                checks["no_fabricated_example"] = not any(
                    term in response for term in ("示例", "随口", "任意")
                )
        return checks

    def _audit_ids(self, store: SQLiteStore) -> set[str]:
        with store.connect() as conn:
            return {str(row["id"]) for row in conn.execute("SELECT id FROM audit_logs")}

    def _new_tool_calls(self, store: SQLiteStore, before_ids: set[str]) -> list[dict[str, Any]]:
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT id, session_id, event_type, payload_json, created_at FROM audit_logs "
                "WHERE event_type = 'tool_call' ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        calls: list[dict[str, Any]] = []
        for row in reversed(rows):
            if str(row["id"]) in before_ids:
                continue
            try:
                payload = store.unseal(row["payload_json"], legacy="json") or {}
            except Exception:
                payload = {}
            call = {
                "audit_id": row["id"],
                "session_id": row["session_id"],
                "event_type": row["event_type"],
                "created_at": row["created_at"],
                "tool_name": payload.get("tool_name") or payload.get("name"),
                "status": payload.get("status"),
            }
            aggregate = payload.get("aggregate")
            if isinstance(aggregate, dict):
                call["aggregate"] = {
                    key: aggregate.get(key)
                    for key in ("TIR", "TAR", "TBR", "MBG", "point_count", "data_coverage")
                    if key in aggregate
                }
            documents = payload.get("documents")
            if isinstance(documents, list):
                call["doc_ids"] = [str(item.get("doc_id")) for item in documents if isinstance(item, dict) and item.get("doc_id")]
            if not call.get("doc_ids") and isinstance(payload.get("evidence_refs"), list):
                derived_ids: list[str] = []
                for ref in payload["evidence_refs"]:
                    if not isinstance(ref, dict) or ref.get("kind") != "authoritative_kb":
                        continue
                    ref_id = str(ref.get("ref_id") or "")
                    if ":" in ref_id:
                        derived_ids.append(ref_id.split(":", 1)[1])
                if derived_ids:
                    call["doc_ids"] = derived_ids
            if payload.get("result_count") is not None:
                call["result_count"] = payload.get("result_count")
            calls.append(call)
        return calls[-30:]

    def _build_client(
        self,
        snapshot: SnapshotManager,
        window: dict[str, Any],
        *,
        data_source: str | None = None,
    ) -> HermesClient:
        provider = self.config.provider or os.getenv("CGM_ACCEPT_PROVIDER", "")
        if not provider:
            provider = self._provider_from_config(snapshot.validation_home / "config.yaml")
        provider = normalize_provider(provider)
        if not provider:
            raise ValueError("a configured custom provider is required; pass --provider")
        return HermesClient(
            executable=Path(self.config.hermes_bin) if self.config.hermes_bin else default_hermes_executable(),
            hermes_home=snapshot.validation_home,
            db_path=snapshot.retrieval_db,
            project_root=self.project_root,
            user_id=self.config.user_id,
            provider=provider,
            model=self.config.model,
            anchor_at=str(window.get("local_end") or "") or None,
            data_source=data_source,
            timezone_name=self.config.timezone_name,
            timeout_seconds=self.config.timeout_seconds,
        )

    @staticmethod
    def _acceptance_data_source(repo: SQLiteCGMRepository) -> str | None:
        with repo.store.connect() as conn:
            row = conn.execute(
                "SELECT source, COUNT(*) AS n FROM glucose_points "
                "WHERE source IS NOT NULL GROUP BY source ORDER BY n DESC, source LIMIT 1"
            ).fetchone()
        source = str(row["source"]).strip() if row and row["source"] else ""
        return source or None

    def _activate_default(self, snapshot: dict[str, Any], final: dict[str, Any]) -> dict[str, Any]:
        provider = normalize_provider(
            self.config.provider
            or os.getenv("CGM_ACCEPT_PROVIDER", "")
            or self._provider_from_config(Path(snapshot["validation_home"]) / "config.yaml")
        )
        data_source = None
        try:
            source_repo = SQLiteCGMRepository(SQLiteStore(Path(self.config.source_db)))
            data_source = self._acceptance_data_source(source_repo)
        except Exception:
            # The cutover still performs its own DB/key checks; absence of a
            # source label should become a normal hard-gate failure rather
            # than leaking an exception before the rollback bundle exists.
            data_source = None
        manager = CutoverManager(
            hermes_bin=Path(self.config.hermes_bin) if self.config.hermes_bin else default_hermes_executable(),
            hermes_home=self._source_home(),
            source_db=Path(self.config.source_db),
            user_id=self.config.user_id,
            project_root=self.project_root,
            provider=provider,
            provider_user_agent=self.config.provider_user_agent,
            provider_max_tokens=self.config.provider_max_tokens,
            model=self.config.model,
            output_dir=self.output_dir,
            run_id=self.run_id,
            deliver=self.config.deliver or self._resolve_delivery_target(self._source_home()),
            max_external_messages=self.config.max_external_messages,
            send_external=self.config.send_external,
            acceptance_dates=list(final.get("window", {}).get("window_days", [])),
            acceptance_events=list(
                final.get("runtime_checks", {}).get("periodic", {}).get("event_alerts", [])
            ),
            data_source=data_source,
        )
        outcome = manager.activate()
        self.ledger.record("cutover", **outcome.to_dict())
        for step in outcome.steps:
            if step.get("name") != "acceptance_correlation":
                continue
            for session_id in step.get("session_ids", []):
                self.ledger.link("run", self.run_id, "session", str(session_id), "acceptance_correlation")
            for audit_id in step.get("audit_ids", []):
                self.ledger.link("run", self.run_id, "audit", str(audit_id), "acceptance_correlation")
            for push_id in step.get("push_ids", []):
                self.ledger.link("run", self.run_id, "push", str(push_id), "acceptance_correlation")
        return outcome.to_dict()

    @staticmethod
    def _resolve_delivery_target(home: Path) -> str | None:
        """Resolve the existing Weixin private target without exposing it."""

        jobs_path = home / "cron" / "jobs.json"
        if not jobs_path.exists():
            return None
        try:
            payload = json.loads(jobs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            target = str(job.get("deliver") or "")
            if target.lower().startswith("weixin:"):
                return target
        return None

    @staticmethod
    def _provider_from_config(path: Path) -> str:
        """Read only provider identifiers from a copied config (never secrets)."""

        if not path.exists():
            return ""
        try:
            import yaml

            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return ""
        entries = config.get("custom_providers")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("provider_key") or entry.get("name") or "").strip()
                if name:
                    return normalize_provider("custom:" + name if not name.lower().startswith("custom:") else name)
        providers = config.get("providers")
        if isinstance(providers, dict):
            for name, entry in providers.items():
                if isinstance(entry, dict) and str(entry.get("base_url") or "").strip():
                    return normalize_provider("custom:" + str(name))
        return ""

    def _source_home(self) -> Path:
        if self.config.hermes_home:
            return Path(self.config.hermes_home)
        return Path(os.getenv("LOCALAPPDATA", Path.home())) / "hermes"

    def _manifest(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        config_path = Path(snapshot["validation_home"]) / "config.yaml"
        return {
            "run_id": self.run_id,
            "project_root": str(self.project_root),
            "code_revision": self._code_revision(),
            "code_worktree_sha256": self._code_worktree_sha256(),
            "git_worktree_dirty": self._git_worktree_dirty(),
            "hermes_version": self._hermes_version(Path(snapshot["validation_home"])),
            "duration_hours": self.config.duration_hours,
            "user_id": self.config.user_id,
            "model": self.config.model,
            "provider_user_agent": self.config.provider_user_agent,
            "provider_max_tokens": self.config.provider_max_tokens,
            "provider": normalize_provider(
                self.config.provider
                or os.getenv("CGM_ACCEPT_PROVIDER", "")
                or self._provider_from_config(Path(snapshot["validation_home"]) / "config.yaml")
            ),
            "max_model_calls": self.config.max_model_calls,
            "max_external_messages": self.config.max_external_messages,
            "configuration": {
                "config_sha256": _sha256(config_path) if config_path.exists() else None,
                "validation_home": str(snapshot["validation_home"]),
            },
            "snapshot": snapshot,
        }

    def _code_revision(self) -> str:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            revision = (completed.stdout or "").strip()
            return revision or "working-tree"
        except OSError:
            return "working-tree"

    def _git_worktree_dirty(self) -> bool:
        try:
            completed = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return bool((completed.stdout or "").strip())
        except OSError:
            return True

    def _code_worktree_sha256(self) -> str:
        """Fingerprint the executable acceptance scope, including untracked code.

        ``git rev-parse HEAD`` alone is insufficient here because the project
        deliberately keeps the current worktree uncommitted.  Hashing source
        and script contents makes the manifest identify the code that actually
        ran without including databases, keys, or model payloads.
        """

        digest = hashlib.sha256()
        files: list[Path] = []
        for relative_root in ("src", "scripts"):
            root = self.project_root / relative_root
            if root.exists():
                files.extend(
                    path
                    for path in root.rglob("*")
                    if path.is_file() and "__pycache__" not in path.parts
                )
        for relative in ("pyproject.toml", ".pre-commit-config.yaml"):
            path = self.project_root / relative
            if path.is_file():
                files.append(path)
        for path in sorted(files):
            relative = path.relative_to(self.project_root).as_posix().encode("utf-8")
            digest.update(relative)
            digest.update(b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        return digest.hexdigest()

    def _hermes_version(self, validation_home: Path) -> str:
        executable = Path(self.config.hermes_bin) if self.config.hermes_bin else default_hermes_executable()
        env = os.environ.copy()
        env["HERMES_HOME"] = str(validation_home)
        try:
            completed = subprocess.run(
                [str(executable), "--version"],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
            return (completed.stdout or completed.stderr or "unknown").strip()[-200:]
        except OSError:
            return "unknown"

    def _final_report(self, window: dict[str, Any], rebuild: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        scenario_count = len(self.scenario_results)
        passed = sum(result.passed for result in self.scenario_results)
        category_counts: dict[str, dict[str, int]] = {}
        for result in self.scenario_results:
            bucket = category_counts.setdefault(result.scenario.category, {"total": 0, "passed": 0})
            bucket["total"] += 1
            bucket["passed"] += int(result.passed)
        style_bucket = category_counts.get("style", {"total": 0, "passed": 0})
        style_rate = (
            style_bucket["passed"] / style_bucket["total"] if style_bucket["total"] else (1.0 if not self.config.run_model else 0.0)
        )
        model_gate = not self.config.run_model or (
            scenario_count == 24 and passed == 24 and style_rate >= 0.95
        )
        replay = rebuild.get("replay") if isinstance(rebuild, dict) else None
        hard_gates = {
            "snapshot": bool(snapshot),
            "window_selected": bool(window),
            "memory_rebuild": bool(
                rebuild
                and rebuild.get("final_counts", {}).get("warm_summaries", 0)
                and all((rebuild.get("promotion_checks") or {}).values())
                and (replay is None or replay.get("idempotent", False))
            ),
            "runtime_memory": bool(
                self.runtime_checks.get("memory")
                and all(self.runtime_checks["memory"].get("checks", {}).values())
            ),
            "runtime_rag": bool(
                self.runtime_checks.get("rag")
                and all(self.runtime_checks["rag"].get("checks", {}).values())
            ),
            "runtime_periodic": bool(
                self.runtime_checks.get("periodic")
                and all(self.runtime_checks["periodic"].get("checks", {}).values())
            ),
            "model_scenarios": model_gate,
            "hermes_restart_recall": self.hermes_restart_ok,
            "activation_request_valid": not self.config.activate_on_pass or self.config.run_model,
            "external_delivery_request_valid": not self.config.send_external or self.config.activate_on_pass,
            "no_issues": not self.issues,
        }
        status = "passed" if all(hard_gates.values()) else "failed"
        return {
            "run_id": self.run_id,
            "status": status,
            "hard_gates": hard_gates,
            "expected": {
                "scenario_count": 24,
                "memory_layers": ["L0", "L1", "L2", "L3", "warm"],
                "max_model_calls": min(self.config.max_model_calls, 30),
                "max_external_messages": min(self.config.max_external_messages, 6),
                "replay_idempotent": True,
            },
            "actual": {
                "scenario_count": scenario_count,
                "model_calls": self.model_calls,
                "external_messages": 0,
                "issues": len(self.issues),
            },
            "issues": self.issues,
            "window": window,
            "rebuild": rebuild,
            "scenario_summary": {
                "total": scenario_count,
                "passed": passed,
                "failed": scenario_count - passed,
                "by_category": category_counts,
                "style_rate": round(style_rate, 4),
            },
            "runtime_checks": self.runtime_checks,
            "external_messages": {"sent": 0, "limit": self.config.max_external_messages},
            "cutover": {"requested": self.config.activate_on_pass, "performed": False, "rolled_back": False},
            "artifacts": {
                "timeline": str(self.output_dir / "timeline.jsonl"),
                "scenarios": str(self.output_dir / "scenario-results.json"),
                "public_scenarios": str(self.output_dir / "public-scenario-results.json"),
                "report": str(self.output_dir / "final-report.json"),
            },
        }

    def _write_timeline(self) -> None:
        path = self.output_dir / "timeline.jsonl"
        path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in self.ledger.records) + "\n", encoding="utf-8")
        write_json(self.output_dir / "links.json", self.ledger.links)

    def _issue(self, stage: str, message: str) -> None:
        self.issues.append({"stage": stage, "message": re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", message)})
        self.ledger.record("issue", issue_stage=stage, message=message)

    @staticmethod
    def _markdown(report: dict[str, Any]) -> str:
        lines = [f"# Hermes CGM Acceptance {report.get('run_id', '')}", "", f"- Status: {report.get('status')}", "", "## Hard gates"]
        for key, value in (report.get("hard_gates") or {}).items():
            lines.append(f"- [{'x' if value else ' '}] `{key}`")
        lines.extend(["", "## Scenario summary", json.dumps(report.get("scenario_summary", {}), ensure_ascii=False)])
        if report.get("issues"):
            lines.extend(["", "## Issues"])
            lines.extend(f"- {item['stage']}: {item['message']}" for item in report["issues"])
        return "\n".join(lines) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_fragment_matches(fragment: str, tool_names: list[str]) -> bool:
    """Match project-facing ``cgm_*`` aliases to Hermes dotted tool names."""

    raw = fragment.lower().strip()
    normalized = raw.removeprefix("cgm_")
    # Hermes keeps the namespace dotted but preserves underscores inside the
    # operation name (``context.get_l0``), while the project-facing alias is
    # ``cgm_context_get_l0``. Splitting only at the first underscore avoids
    # turning ``get_l0`` into the incorrect ``get.l0``.
    if "." not in normalized and "_" in normalized:
        namespace, operation = normalized.split("_", 1)
        normalized = f"{namespace}.{operation}"
    return any(raw in name or normalized in name for name in tool_names)
