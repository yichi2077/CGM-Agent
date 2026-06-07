from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from hermes_cgm_agent.config import default_hermes_exe


ROOT_MARKER_NAME = "cgm-agent-project-root.txt"
PLUGIN_NAMES = ("cgm", "cgm_memory")


@dataclass(frozen=True)
class HermesInstallReport:
    project_root: str
    hermes_home: str
    hermes_bin: str
    plugin_targets: dict[str, str]
    editable_install_python: str | None = None
    actions: list[str] = field(default_factory=list)
    smoke_checks: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def install_hermes_integration(
    *,
    project_root: Path | None = None,
    hermes_home: Path | None = None,
    hermes_bin: str | None = None,
    install_editable: bool = True,
    configure_runtime: bool = True,
    smoke: bool = False,
    dry_run: bool = False,
) -> HermesInstallReport:
    root = _resolve_project_root(project_root)
    home = _resolve_hermes_home(hermes_home)
    bin_path = _resolve_hermes_bin(hermes_bin)

    plugins_dir = home / "plugins"
    if not dry_run:
        plugins_dir.mkdir(parents=True, exist_ok=True)
    actions: list[str] = []
    plugin_targets: dict[str, str] = {}

    for plugin_name in PLUGIN_NAMES:
        source = root / "integrations" / "hermes" / plugin_name
        target = plugins_dir / plugin_name
        plugin_targets[plugin_name] = str(target)
        if dry_run:
            actions.append(f"would-install:{plugin_name}:{target}")
        else:
            _install_plugin_dir(source=source, target=target)
            actions.append(f"installed:{plugin_name}")

    marker_path = home / ROOT_MARKER_NAME
    if dry_run:
        actions.append(f"would-write-marker:{marker_path}")
    else:
        marker_path.write_text(str(root), encoding="utf-8")
        actions.append(f"wrote-marker:{marker_path}")

    editable_python = _editable_runtime_python(home)
    if install_editable and editable_python is not None:
        if dry_run:
            actions.append(f"would-editable-install:{editable_python}")
        else:
            subprocess.run(
                [str(editable_python), "-m", "pip", "install", "-e", str(root)],
                check=True,
                capture_output=True,
                text=True,
            )
            actions.append(f"editable-install:{editable_python}")

    if configure_runtime:
        if dry_run:
            actions.append("would-enable-plugin:cgm")
            actions.append("would-enable-memory-provider:cgm_memory")
        else:
            subprocess.run([bin_path, "plugins", "enable", "cgm"], check=True, capture_output=True, text=True)
            actions.append("enabled-plugin:cgm")
            subprocess.run([bin_path, "memory", "setup", "cgm_memory"], check=True, capture_output=True, text=True)
            actions.append("enabled-memory-provider:cgm_memory")

    smoke_checks: dict[str, bool] = {}
    if smoke:
        if dry_run:
            actions.append("would-smoke:hermes-plugins-list")
            actions.append("would-smoke:hermes-memory-status")
            actions.append("would-smoke:cgm-dev-status")
            smoke_checks = {
                "hermes_plugins_list": False,
                "hermes_memory_status": False,
                "cgm_dev_status": False,
            }
        else:
            smoke_python = editable_python or Path(sys.executable)
            _run_checked([bin_path, "plugins", "list", "--plain", "--no-bundled"], cwd=root)
            actions.append("smoke:hermes-plugins-list")
            smoke_checks["hermes_plugins_list"] = True
            _run_checked([bin_path, "memory", "status"], cwd=root)
            actions.append("smoke:hermes-memory-status")
            smoke_checks["hermes_memory_status"] = True
            _run_checked([str(smoke_python), "-m", "hermes_cgm_agent", "dev-status"], cwd=root)
            actions.append("smoke:cgm-dev-status")
            smoke_checks["cgm_dev_status"] = True

    return HermesInstallReport(
        project_root=str(root),
        hermes_home=str(home),
        hermes_bin=bin_path,
        plugin_targets=plugin_targets,
        editable_install_python=str(editable_python) if editable_python is not None else None,
        actions=actions,
        smoke_checks=smoke_checks,
    )


def _resolve_project_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        root = explicit.expanduser().resolve()
    elif os.environ.get("CGM_AGENT_PROJECT_ROOT"):
        root = Path(os.environ["CGM_AGENT_PROJECT_ROOT"]).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[3]
    if not (root / "integrations" / "hermes").is_dir():
        raise FileNotFoundError(
            "Hermes CGM plugin sources were not found. Run from an editable "
            "checkout or pass --project-root /path/to/CGM-Agent."
        )
    return root


def _resolve_hermes_home(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".hermes").resolve()


def _resolve_hermes_bin(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    discovered = shutil.which("hermes")
    if discovered:
        return discovered
    fallback = default_hermes_exe()
    return str(fallback) if fallback is not None else "hermes"


def _editable_runtime_python(hermes_home: Path) -> Path | None:
    if sys.platform.startswith("win"):
        candidate = hermes_home / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    else:
        candidate = hermes_home / "hermes-agent" / "venv" / "bin" / "python3"
    return candidate if candidate.exists() else None


def _install_plugin_dir(*, source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"Hermes plugin source not found: {source}")

    if target.exists() or target.is_symlink():
        try:
            if target.resolve() == source.resolve():
                return
        except OSError:
            pass
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)

    try:
        target.symlink_to(source, target_is_directory=True)
    except OSError:
        shutil.copytree(source, target, symlinks=True)


def _run_checked(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True, cwd=str(cwd))
