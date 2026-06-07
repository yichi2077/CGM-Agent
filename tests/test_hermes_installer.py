from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.hermes_plugins.installer import (
    ROOT_MARKER_NAME,
    install_hermes_integration,
    _resolve_project_root,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class HermesInstallerTests(unittest.TestCase):
    def test_install_creates_plugin_links_and_marker(self) -> None:
        with tempfile.TemporaryDirectory() as hermes_home:
            report = install_hermes_integration(
                project_root=PROJECT_ROOT,
                hermes_home=Path(hermes_home),
                hermes_bin="hermes",
                install_editable=False,
                configure_runtime=False,
            )

            self.assertEqual(set(report.plugin_targets), {"cgm", "cgm_memory"})
            for name, target in report.plugin_targets.items():
                path = Path(target)
                self.assertTrue(path.exists())
                self.assertTrue(path.is_symlink() or path.is_dir())
                self.assertEqual(path.resolve(), (PROJECT_ROOT / "integrations" / "hermes" / name).resolve())

            marker = Path(hermes_home) / ROOT_MARKER_NAME
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), str(PROJECT_ROOT))

    def test_install_runs_hermes_runtime_configuration_commands(self) -> None:
        with tempfile.TemporaryDirectory() as hermes_home:
            python_dir = Path(hermes_home) / "hermes-agent" / "venv" / "bin"
            python_dir.mkdir(parents=True)
            python_bin = python_dir / "python3"
            python_bin.write_text("", encoding="utf-8")

            with patch("hermes_cgm_agent.hermes_plugins.installer.subprocess.run") as run:
                report = install_hermes_integration(
                    project_root=PROJECT_ROOT,
                    hermes_home=Path(hermes_home),
                    hermes_bin="hermes",
                )

            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn(
                [str(python_bin.resolve()), "-m", "pip", "install", "-e", str(PROJECT_ROOT)],
                commands,
            )
            self.assertIn(["hermes", "plugins", "enable", "cgm"], commands)
            self.assertIn(["hermes", "memory", "setup", "cgm_memory"], commands)
            self.assertIn("enabled-memory-provider:cgm_memory", report.actions)

    def test_install_smoke_runs_runtime_and_cgm_status_checks(self) -> None:
        with tempfile.TemporaryDirectory() as hermes_home:
            python_dir = Path(hermes_home) / "hermes-agent" / "venv" / "bin"
            python_dir.mkdir(parents=True)
            python_bin = python_dir / "python3"
            python_bin.write_text("", encoding="utf-8")

            with patch("hermes_cgm_agent.hermes_plugins.installer.subprocess.run") as run:
                report = install_hermes_integration(
                    project_root=PROJECT_ROOT,
                    hermes_home=Path(hermes_home),
                    hermes_bin="hermes",
                    install_editable=False,
                    configure_runtime=False,
                    smoke=True,
                )

            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn(["hermes", "plugins", "list", "--plain", "--no-bundled"], commands)
            self.assertIn(["hermes", "memory", "status"], commands)
            self.assertIn(
                [str(python_bin.resolve()), "-m", "hermes_cgm_agent", "dev-status"],
                commands,
            )
            self.assertEqual(
                report.smoke_checks,
                {
                    "hermes_plugins_list": True,
                    "hermes_memory_status": True,
                    "cgm_dev_status": True,
                },
            )

    def test_dry_run_reports_actions_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as hermes_home:
            with patch("hermes_cgm_agent.hermes_plugins.installer.subprocess.run") as run:
                report = install_hermes_integration(
                    project_root=PROJECT_ROOT,
                    hermes_home=Path(hermes_home),
                    hermes_bin="hermes",
                    dry_run=True,
                )

            self.assertFalse((Path(hermes_home) / "plugins").exists())
            self.assertFalse((Path(hermes_home) / ROOT_MARKER_NAME).exists())
            self.assertIn("would-enable-plugin:cgm", report.actions)
            self.assertIn("would-enable-memory-provider:cgm_memory", report.actions)
            run.assert_not_called()

    def test_project_root_can_resolve_from_environment(self) -> None:
        with patch.dict("os.environ", {"CGM_AGENT_PROJECT_ROOT": str(PROJECT_ROOT)}):
            self.assertEqual(_resolve_project_root(), PROJECT_ROOT.resolve())

    def test_project_root_error_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(FileNotFoundError, "--project-root"):
                _resolve_project_root(Path(temp_dir))
