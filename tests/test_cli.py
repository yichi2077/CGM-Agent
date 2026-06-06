from __future__ import annotations

import unittest

from hermes_cgm_agent.cli import build_parser


class CliTests(unittest.TestCase):
    def test_status_command_parses(self) -> None:
        args = build_parser().parse_args(["status"])
        self.assertEqual(args.command, "status")

    def test_dev_status_command_parses(self) -> None:
        args = build_parser().parse_args(["dev-status"])
        self.assertEqual(args.command, "dev-status")

    def test_tools_command_parses_filters(self) -> None:
        args = build_parser().parse_args(["tools", "--group", "timeseries", "--status", "planned"])
        self.assertEqual(args.command, "tools")
        self.assertEqual(args.group, "timeseries")
        self.assertEqual(args.status, "planned")

    def test_import_cgm_command_parses_required_arguments(self) -> None:
        args = build_parser().parse_args(
            [
                "import-cgm",
                "--file",
                "sample.csv",
                "--format",
                "csv",
                "--user-id",
                "user-1",
                "--timezone",
                "Asia/Shanghai",
            ]
        )

        self.assertEqual(args.command, "import-cgm")
        self.assertEqual(args.file, "sample.csv")
        self.assertEqual(args.format, "csv")
        self.assertEqual(args.user_id, "user-1")
        self.assertEqual(args.timezone, "Asia/Shanghai")

    def test_tool_call_command_parses_required_arguments(self) -> None:
        args = build_parser().parse_args(
            [
                "tool-call",
                "reports.generate",
                "--input",
                "report.json",
                "--session-id",
                "manual-session",
            ]
        )

        self.assertEqual(args.command, "tool-call")
        self.assertEqual(args.tool_name, "reports.generate")
        self.assertEqual(args.input, "report.json")
        self.assertEqual(args.session_id, "manual-session")

    def test_dexcom_auth_command_parses(self) -> None:
        args = build_parser().parse_args(
            ["dexcom-auth", "--user-id", "user-1", "--code", "abc123"]
        )
        self.assertEqual(args.command, "dexcom-auth")
        self.assertEqual(args.user_id, "user-1")
        self.assertEqual(args.code, "abc123")

    def test_dexcom_sync_command_parses(self) -> None:
        args = build_parser().parse_args(
            ["dexcom-sync", "--user-id", "user-1", "--days", "14", "--force"]
        )
        self.assertEqual(args.command, "dexcom-sync")
        self.assertEqual(args.user_id, "user-1")
        self.assertEqual(args.days, 14)
        self.assertTrue(args.force)

    def test_memory_synthesize_command_parses(self) -> None:
        args = build_parser().parse_args(
            [
                "memory-synthesize",
                "--user-id",
                "user-1",
                "--window-start",
                "2026-05-31T00:00:00+00:00",
                "--window-end",
                "2026-06-01T00:00:00+00:00",
                "--period",
                "daily",
            ]
        )
        self.assertEqual(args.command, "memory-synthesize")
        self.assertEqual(args.user_id, "user-1")
        self.assertEqual(args.period, "daily")

    def test_context_build_command_parses(self) -> None:
        args = build_parser().parse_args(
            [
                "context-build",
                "--user-id",
                "user-1",
                "--anchor-at",
                "2026-06-15T00:00:00+00:00",
                "--source",
                "sensor:test",
            ]
        )
        self.assertEqual(args.command, "context-build")
        self.assertEqual(args.user_id, "user-1")
        self.assertEqual(args.source, "sensor:test")

    def test_kb_ingest_llm_command_parses(self) -> None:
        args = build_parser().parse_args(
            [
                "kb-ingest-llm",
                "--pdf",
                "guideline.pdf",
                "--out-dir",
                "review",
                "--kb-version",
                "kb-test",
                "--pages",
                "1-5",
                "--mode",
                "vision",
                "--engine",
                "sentence",
            ]
        )
        self.assertEqual(args.command, "kb-ingest-llm")
        self.assertEqual(args.mode, "vision")
        self.assertEqual(args.engine, "sentence")

    def test_kb_merge_and_eval_commands_parse(self) -> None:
        merge_args = build_parser().parse_args(
            ["kb-merge", "--candidates", "review/a.candidates.json", "--dry-run"]
        )
        eval_args = build_parser().parse_args(["eval-rag", "--queries", "eval/rag/queries.jsonl"])
        self.assertEqual(merge_args.command, "kb-merge")
        self.assertTrue(merge_args.dry_run)
        self.assertEqual(eval_args.command, "eval-rag")

    def test_kb_ingest_command_parses(self) -> None:
        args = build_parser().parse_args(
            [
                "kb-ingest",
                "--pdf",
                "guideline.pdf",
                "--out-dir",
                "review",
                "--kb-version",
                "kb-test",
            ]
        )
        self.assertEqual(args.command, "kb-ingest")
        self.assertEqual(args.pdf, "guideline.pdf")
        self.assertEqual(args.kb_version, "kb-test")

    def test_hermes_install_command_parses_flags(self) -> None:
        args = build_parser().parse_args(
            [
                "hermes-install",
                "--project-root",
                "/tmp/project",
                "--hermes-home",
                "/tmp/hermes",
                "--hermes-bin",
                "hermes",
                "--skip-editable-install",
                "--dry-run",
            ]
        )

        self.assertEqual(args.command, "hermes-install")
        self.assertEqual(args.project_root, "/tmp/project")
        self.assertEqual(args.hermes_home, "/tmp/hermes")
        self.assertEqual(args.hermes_bin, "hermes")
        self.assertTrue(args.skip_editable_install)
        self.assertTrue(args.dry_run)


if __name__ == "__main__":
    unittest.main()
