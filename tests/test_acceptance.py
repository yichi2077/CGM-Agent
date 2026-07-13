from __future__ import annotations

import unittest
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.services.acceptance.cutover import CutoverManager, CutoverOutcome, DeliveryBudget
from hermes_cgm_agent.services.acceptance.hermes import _safe_error, normalize_provider
from hermes_cgm_agent.services.acceptance.models import AcceptanceConfig
from hermes_cgm_agent.services.acceptance.models import Scenario, ScenarioResult
from hermes_cgm_agent.services.acceptance.oracle import (
    build_scenarios,
    numeric_claims_supported,
    style_checks,
)
from hermes_cgm_agent.services.acceptance.runner import AcceptanceRunner
from hermes_cgm_agent.services.acceptance.runtime import validate_rag_runtime
from hermes_cgm_agent.services.acceptance.snapshot import SnapshotManager
from hermes_cgm_agent.services.tools.executor import (
    _apply_acceptance_time_anchor,
    _apply_acceptance_data_source,
    _fill_default_user_id,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class AcceptanceContractTests(unittest.TestCase):
    def test_manifest_has_exactly_24_scenarios_and_expected_categories(self) -> None:
        scenarios = build_scenarios(None, "user-1", window={})
        self.assertEqual(len(scenarios), 24)
        categories = {scenario.category for scenario in scenarios}
        self.assertEqual(categories, {"memory", "rag", "style", "negative"})

    def test_model_prompts_keep_internal_labels_and_unbacked_month_numbers_out(self) -> None:
        scenarios = {item.scenario_id: item for item in build_scenarios(None, "user-1", window={})}
        self.assertIn("L0", scenarios["memory-l3-01"].prompt)
        self.assertIn("不要输出或提及", scenarios["memory-l3-01"].prompt)
        monthly_prompt = scenarios["style-monthly"].prompt
        self.assertIn("百分比保留一位小数", monthly_prompt)
        self.assertIn("不要编造或推断数据覆盖了几天", monthly_prompt)

    def test_public_scenario_artifact_redacts_payload_and_identity_fields(self) -> None:
        scenario = Scenario(scenario_id="style-current", category="style", prompt="prompt")
        item = ScenarioResult(
            scenario=scenario,
            status="passed",
            response="private answer",
            exit_code=0,
            duration_seconds=1.0,
            tool_calls=[
                {
                    "tool_name": "cgm_timeseries_get_points",
                    "status": "ok",
                    "audit_id": "secret-audit",
                    "session_id": "secret-session",
                    "doc_ids": ["card-1"],
                    "payload": {"user_id": "private-user", "value": 123},
                }
            ],
            oracle={
                "user_id": "private-user",
                "point_count": 10,
                "event_count": 1,
                "event_types": {"hypo": 1},
                "aggregate": {"MBG": 123},
            },
        )
        public = item.to_dict(redact_response=True)
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertEqual(public["response"], "[REDACTED]")
        self.assertNotIn("secret-audit", serialized)
        self.assertNotIn("secret-session", serialized)
        self.assertNotIn("private-user", serialized)
        self.assertNotIn('"MBG"', serialized)
        self.assertEqual(public["oracle"]["point_count"], 10)

    def test_acceptance_correlation_links_cron_session_audit_and_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "app.db"
            store = SQLiteStore(db_path)
            store.initialize()
            audit_id = store.create_audit_log(
                session_id="cron_job123_20260713_120000",
                event_type="tool_call",
                payload={"tool_name": "delivery.send", "push_id": "push-1"},
            )
            manager = CutoverManager(
                hermes_bin=Path("hermes"),
                hermes_home=root / "hermes",
                source_db=db_path,
                user_id="u1",
                project_root=Path.cwd(),
                provider="custom:example",
                model="gpt-5.5",
                output_dir=root / "artifacts",
                run_id="run-correlation",
            )
            outcome = CutoverOutcome(requested=True)
            manager._record_acceptance_links(outcome, "job123", "run-correlation:event:1")

            link = next(step for step in outcome.steps if step["name"] == "acceptance_correlation")
            self.assertEqual(link["status"], "ok")
            self.assertIn(audit_id, link["audit_ids"])
            self.assertEqual(link["push_ids"], ["push-1"])
            self.assertFalse(outcome.issues)

    def test_style_gate_rejects_internal_leaks_and_long_text(self) -> None:
        result = style_checks("L0 cgm_context_get_l0 " + ("x" * 100), max_chars=20)
        self.assertFalse(result["passed"])
        self.assertIn("l0", result["forbidden_terms"])
        self.assertFalse(result["within_length"])

    def test_empty_style_diagnostics_do_not_fail_boolean_gate(self) -> None:
        scenario = next(
            item for item in build_scenarios(None, "user-1", window={})
            if item.scenario_id == "style-current"
        )
        runner = AcceptanceRunner(
            AcceptanceConfig(source_db="unused", user_id="user-1", run_model=False)
        )
        checks = runner._scenario_checks(
            scenario,
            "现在整体平稳。",
            0,
            [],
            {"aggregate": {}},
        )
        self.assertTrue(checks["passed"])

    def test_safe_error_redacts_provider_credentials(self) -> None:
        result = _safe_error("HTTP 403 sk-secret-token", "Bearer abc")
        self.assertNotIn("sk-secret-token", result)
        self.assertNotIn("Bearer abc", result)

    def test_custom_provider_normalization_matches_hermes_slug(self) -> None:
        self.assertEqual(
            normalize_provider("custom:Sub2.sweethzm.xyz"),
            "custom:sub2.sweethzm.xyz",
        )
        self.assertEqual(normalize_provider("deepseek"), "deepseek")

    def test_delivery_budget_is_hard_capped(self) -> None:
        budget = DeliveryBudget(2)
        self.assertTrue(budget.reserve())
        self.assertTrue(budget.reserve())
        self.assertFalse(budget.reserve())
        self.assertEqual(budget.sent, 2)

    def test_acceptance_jobs_use_oracle_dates_events_and_run_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "hermes"
            (home / "cron").mkdir(parents=True)
            jobs_path = home / "cron" / "jobs.json"
            jobs_path.write_text(json.dumps({"jobs": []}), encoding="utf-8")
            manager = CutoverManager(
                hermes_bin=Path("hermes"),
                hermes_home=home,
                source_db=root / "app.db",
                user_id="u1",
                project_root=Path.cwd(),
                provider="custom:example",
                model="gpt-5.5",
                output_dir=root / "artifacts",
                run_id="run-accept",
                deliver="weixin:private",
                max_external_messages=6,
                send_external=True,
                acceptance_dates=["2026-06-30", "2026-07-01", "2026-07-02"],
                acceptance_events=[
                    {"date": "2026-06-30", "event_id": "event-1", "event_type": "rapid_fall"},
                    {"date": "2026-07-01", "event_id": "event-2", "event_type": "rapid_rise"},
                    {"date": "2026-07-02", "event_id": "event-3", "event_type": "hypo"},
                ],
            )
            outcome = CutoverOutcome(requested=True)
            prompts: list[str] = []

            def fake_step(outcome, args, name, record_issue=True):
                if args[:2] == ["cron", "create"]:
                    prompt = str(args[3])
                    prompts.append(prompt)
                    job_name = str(args[args.index("--name") + 1])
                    payload = json.loads(jobs_path.read_text(encoding="utf-8"))
                    payload["jobs"].append({"id": job_name, "name": job_name, "last_status": None})
                    jobs_path.write_text(json.dumps(payload), encoding="utf-8")
                    outcome.steps.append({"name": name, "status": "ok", "exit_code": 0})
                    return "Created job"
                if args[:2] == ["cron", "run"]:
                    job_id = str(args[2])
                    payload = json.loads(jobs_path.read_text(encoding="utf-8"))
                    for job in payload["jobs"]:
                        if job["id"] == job_id:
                            job["last_status"] = "ok"
                    jobs_path.write_text(json.dumps(payload), encoding="utf-8")
                    outcome.steps.append({"name": name, "status": "ok", "exit_code": 0})
                    return "Triggered\nRan now: succeeded."
                raise AssertionError(args)

            with patch.object(manager, "_run_step", side_effect=fake_step):
                manager.create_acceptance_jobs(outcome)

            self.assertFalse(outcome.issues)
            self.assertEqual(outcome.external_messages, 5)
            self.assertEqual(len([step for step in outcome.steps if step["name"] == "acceptance_link"]), 5)
            self.assertTrue(all("run-accept" in prompt for prompt in prompts))
            self.assertTrue(any("event-1" in prompt for prompt in prompts))
            self.assertTrue(any("模拟日期=2026-07-02" in prompt for prompt in prompts))

    def test_acceptance_job_delivery_failure_is_a_hard_gate_even_after_cron_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = CutoverManager(
                hermes_bin=Path("hermes"),
                hermes_home=root / "hermes",
                source_db=root / "app.db",
                user_id="u1",
                project_root=Path.cwd(),
                provider="custom:example",
                model="gpt-5.5",
                output_dir=root / "artifacts",
                run_id="run-delivery-error",
                deliver="weixin:private",
                send_external=True,
            )
            outcome = CutoverOutcome(requested=True)
            manager._check_job_execution(
                outcome,
                "job-1",
                "Triggered\nRan now: succeeded\nDelivery failed: iLink sendmessage rate limited",
                "run_acceptance_job:daily:1",
            )
            self.assertTrue(any("delivery failure" in issue for issue in outcome.issues))

    def test_canary_contains_simulated_date_and_correlation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = CutoverManager(
                hermes_bin=Path("hermes"),
                hermes_home=root / "hermes",
                source_db=root / "app.db",
                user_id="u1",
                project_root=Path.cwd(),
                provider="custom:example",
                model="gpt-5.5",
                output_dir=root / "artifacts",
                run_id="run-canary",
                deliver="weixin:private",
                max_external_messages=6,
                send_external=True,
                acceptance_dates=["2026-06-30", "2026-07-01", "2026-07-02"],
            )
            outcome = CutoverOutcome(requested=True)
            captured: list[list[str]] = []

            def fake_step(outcome, args, name, record_issue=True):
                captured.append(list(args))
                outcome.steps.append({"name": name, "status": "ok", "exit_code": 0})
                return "sent"

            with patch.object(manager, "_run_step", side_effect=fake_step):
                manager._send_canary(outcome)

            self.assertEqual(outcome.external_messages, 1)
            self.assertIn("模拟日期=2026-07-02", captured[0][-1])
            self.assertIn("correlation_id=run-canary:canary:2026-07-02", captured[0][-1])
            correlation = next(step for step in outcome.steps if step["name"] == "canary_correlation")
            self.assertEqual(correlation["status"], "ok")

    def test_provider_user_agent_is_scoped_to_validation_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = SnapshotManager(root / "source.db", root / "run", root / "hermes")
            manager.validation_home.mkdir(parents=True)
            config_path = manager.validation_home / "config.yaml"
            config_path.write_text(
                "model:\n  provider: custom:example\n  default: gpt-5.5\n",
                encoding="utf-8",
            )
            result = manager.configure_provider_headers("curl/8.10.1")
            self.assertEqual(result, {"configured": True, "user_agent": "curl/8.10.1"})
            self.assertIn("User-Agent: curl/8.10.1", config_path.read_text(encoding="utf-8"))
            self.assertEqual(manager.configure_provider_headers(None), {"configured": False})
            with self.assertRaises(ValueError):
                manager.configure_provider_headers("bad\r\nheader")

    def test_acceptance_enforces_one_user_id_at_tool_boundary(self) -> None:
        with patch.dict(
            "os.environ",
            {"CGM_AGENT_USER_ID": "demo-prediabetes-14d-v2", "CGM_AGENT_ENFORCE_USER_ID": "1"},
            clear=False,
        ):
            result = _fill_default_user_id(
                {"user_id": "default", "data_scope": {"user_id": "default"}}
            )
        self.assertEqual(result["user_id"], "demo-prediabetes-14d-v2")
        self.assertEqual(result["data_scope"]["user_id"], "demo-prediabetes-14d-v2")

    def test_acceptance_time_anchor_preserves_window_duration(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CGM_AGENT_ENFORCE_TIME_ANCHOR": "1",
                "CGM_AGENT_ACCEPTANCE_ANCHOR_AT": "2026-07-02T16:00:00Z",
                "CGM_AGENT_ACCEPTANCE_TIMEZONE": "Asia/Shanghai",
            },
            clear=False,
        ):
            result = _apply_acceptance_time_anchor(
                {
                    "data_scope": {
                        "window_start": "2026-07-12T11:00:00Z",
                        "window_end": "2026-07-12T12:00:00Z",
                    },
                    "anchor_at": "2026-07-12T12:00:00Z",
                }
            )
        self.assertEqual(result["anchor_at"], "2026-07-02T16:00:00Z")
        start = datetime.fromisoformat(
            result["data_scope"]["window_start"].replace("Z", "+00:00")
        )
        end = datetime.fromisoformat(
            result["data_scope"]["window_end"].replace("Z", "+00:00")
        )
        self.assertEqual((end - start).total_seconds(), 3600)

    def test_acceptance_time_anchor_aligns_model_local_scope_end(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CGM_AGENT_ENFORCE_TIME_ANCHOR": "1",
                "CGM_AGENT_ACCEPTANCE_ANCHOR_AT": "2026-07-02T16:00:00Z",
            },
            clear=False,
        ):
            result = _apply_acceptance_time_anchor(
                {
                    "data_scope": {
                        "window_start": "2026-07-12T11:00:00+08:00",
                        "window_end": "2026-07-12T12:00:00+08:00",
                    }
                }
            )
        self.assertEqual(result["data_scope"]["window_end"], "2026-07-02T16:00:00Z")
        self.assertEqual(result["data_scope"]["window_start"], "2026-07-02T15:00:00Z")

    def test_acceptance_time_anchor_respects_calendar_month_label(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CGM_AGENT_ENFORCE_TIME_ANCHOR": "1",
                "CGM_AGENT_ACCEPTANCE_ANCHOR_AT": "2026-07-03T00:00:00+08:00",
                "CGM_AGENT_ACCEPTANCE_TIMEZONE": "Asia/Shanghai",
            },
            clear=False,
        ):
            result = _apply_acceptance_time_anchor(
                {
                    "data_scope": {
                        "window_start": "2026-06-01T00:00:00+08:00",
                        "window_end": "2026-07-12T00:00:00+08:00",
                    },
                    "window_label": "month",
                }
            )
        self.assertEqual(result["data_scope"]["window_start"], "2026-06-30T16:00:00Z")
        self.assertEqual(result["data_scope"]["window_end"], "2026-07-02T16:00:00Z")

    def test_acceptance_data_source_overrides_model_label_only_in_isolation(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CGM_AGENT_ENFORCE_DATA_SOURCE": "1",
                "CGM_AGENT_ACCEPTANCE_SOURCE": "virtual:aidex-v2",
            },
            clear=False,
        ):
            result = _apply_acceptance_data_source(
                {"data_scope": {"source": "dexcom", "user_id": "user-1"}}
            )
        self.assertEqual(result["data_scope"]["source"], "virtual:aidex-v2")

    def test_local_rag_runtime_gate_checks_all_six_topics(self) -> None:
        result = validate_rag_runtime(build_scenarios(None, "user-1", window={}))
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(len(result["scenarios"]), 6)

    def test_numeric_claim_gate_rejects_unbacked_units(self) -> None:
        oracle = {"aggregate": {"MBG": 120.0, "TIR": 88.0}, "rag_numbers": []}
        self.assertTrue(numeric_claims_supported("平均约 6.7 mmol/L，目标时间 88%。", oracle))
        self.assertFalse(numeric_claims_supported("现在是 999 mg/dL。", oracle))

    def test_numeric_claim_gate_accepts_realtime_mmol_conversion(self) -> None:
        oracle = {
            "aggregate": {"MBG": 140.0},
            "realtime_facts": {
                "latest_mg_dl": 145.8,
                "recent_mbg": 140.0,
                "delta_15m_mg_dl": -8.6,
                "delta_30m_mg_dl": -9.3,
            },
        }
        self.assertTrue(
            numeric_claims_supported(
                "当前约 146 mg/dL（8.1 mmol/L），近期平均 140 mg/dL（7.8 mmol/L），下降 8.6 和 9.3 mg/dL。",
                oracle,
            )
        )

    def test_negative_number_gate_does_not_round_fabricated_precision(self) -> None:
        oracle = {"aggregate": {"MBG": 120.0}, "event_numbers": [123.4]}
        self.assertFalse(
            numeric_claims_supported("数据里没有这个值：123.456 mg/dL。", oracle, strict=True)
        )

    def test_cutover_backup_and_rollback_restore_profile_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "hermes"
            home.mkdir()
            (home / "cron").mkdir()
            (home / "config.yaml").write_text(
                "model:\n  provider: deepseek\n  default: old\n"
                "custom_providers:\n  - name: Example\n    base_url: https://example.invalid/v1\n",
                encoding="utf-8",
            )
            (home / ".env").write_text("KEEP=1\n", encoding="utf-8")
            (home / "cron" / "jobs.json").write_text(
                json.dumps({"jobs": [{"id": "old", "name": "cgm-old", "enabled": True}]}),
                encoding="utf-8",
            )
            source_db = root / "source.db"
            store = SQLiteStore(source_db)
            store.initialize()
            key = source_db.parent / "storage.key"
            original_config = (home / "config.yaml").read_text(encoding="utf-8")

            def fake_run(command, **kwargs):
                joined = " ".join(str(part) for part in command)
                if "memory status" in joined:
                    stdout = "Provider: cgm_memory"
                elif "plugins list" in joined or "tools list" in joined:
                    stdout = "cgm"
                else:
                    stdout = "ok"
                return __import__("subprocess").CompletedProcess(command, 0, stdout, "")

            manager = CutoverManager(
                hermes_bin=Path("hermes"),
                hermes_home=home,
                source_db=source_db,
                user_id="u1",
                project_root=Path.cwd(),
                provider="custom:Example",
                model="gpt-5.5",
                output_dir=root / "artifacts",
                run_id="run-test",
                deliver="weixin:private",
            )
            with patch("subprocess.run", side_effect=fake_run):
                outcome = manager.activate()
                activated_config = (home / "config.yaml").read_text(encoding="utf-8")
                manager.rollback(outcome)
            self.assertTrue(outcome.performed)
            self.assertIn("gpt-5.5", activated_config)
            self.assertEqual((home / "config.yaml").read_text(encoding="utf-8"), original_config)
            self.assertTrue(outcome.rolled_back)
            self.assertTrue(key.exists())


if __name__ == "__main__":
    unittest.main()
