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
            ]
        )

        self.assertEqual(args.command, "hermes-install")
        self.assertEqual(args.project_root, "/tmp/project")
        self.assertEqual(args.hermes_home, "/tmp/hermes")
        self.assertEqual(args.hermes_bin, "hermes")
        self.assertTrue(args.skip_editable_install)


if __name__ == "__main__":
    unittest.main()
