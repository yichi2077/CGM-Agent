from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


CGM_REPORTS_GENERATE_SCHEMA = {
    "name": "cgm_reports_generate",
    "description": "Call the local hermes-cgm-agent reports.generate tool through the project CLI boundary.",
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "user_id": {"type": "string"},
            "report_type": {"type": "string", "enum": ["daily", "weekly", "doctor"]},
            "data_scope": {"type": "object"},
            "timezone": {"type": "string"},
            "report_anchor_time": {"type": "string"},
        },
        "required": ["user_id", "report_type"],
    },
}


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="cgm_reports_generate",
        toolset="cgm",
        schema=CGM_REPORTS_GENERATE_SCHEMA,
        handler=_handle_cgm_reports_generate,
        description="Generate a local CGM report via hermes-cgm-agent.",
    )


def _handle_cgm_reports_generate(args: dict[str, Any], **_: Any) -> str:
    project_root = os.environ.get("CGM_AGENT_PROJECT_ROOT")
    if not project_root:
        return _result(
            {
                "status": "error",
                "error": "CGM_AGENT_PROJECT_ROOT must point to the hermes-cgm-agent project root.",
            }
        )
    root = Path(project_root)
    session_id = str(args.get("session_id") or "hermes-cgm-plugin-session")
    tool_args = {key: value for key, value in args.items() if key != "session_id"}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(tool_args, handle, ensure_ascii=True, sort_keys=True)
        input_path = handle.name
    env = {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
    }
    command = [
        sys.executable,
        "-m",
        "hermes_cgm_agent",
        "tool-call",
        "reports.generate",
        "--input",
        input_path,
        "--session-id",
        session_id,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    finally:
        Path(input_path).unlink(missing_ok=True)
    if completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip()
    return _result(
        {
            "status": "error",
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    )


def _result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)
