from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

from hermes_cgm_agent.domain import (
    DataScope,
    DeviceSession,
    EvidenceRef,
    GlucoseAggregate,
    GlucoseEvent,
    GlucosePoint,
    RawCGMRecord,
    RawImportBatch,
    Report,
    UserEvent,
)
from hermes_cgm_agent.hermes_plugins import install_hermes_integration
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import (
    CGMImporter,
    CGMNormalizer,
    NormalizationConfig,
    SQLiteCGMRepository,
)
from hermes_cgm_agent.services.tools import ToolExecutor, build_default_tool_registry
from hermes_cgm_agent.config import AppConfig, default_hermes_exe
from hermes_cgm_agent.storage.sqlite import SQLiteStore


DOMAIN_MODELS = [
    RawCGMRecord.__name__,
    RawImportBatch.__name__,
    GlucosePoint.__name__,
    DeviceSession.__name__,
    UserEvent.__name__,
    GlucoseAggregate.__name__,
    GlucoseEvent.__name__,
    DataScope.__name__,
    EvidenceRef.__name__,
    Report.__name__,
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cgm-agent",
        description="Hermes-backed personal CGM agent project shell",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show project and Hermes platform status")
    sub.add_parser("dev-status", help="Show an auditable development status snapshot")
    sub.add_parser("hermes-version", help="Print Hermes version details")
    tools = sub.add_parser("tools", help="List planned Hermes-facing CGM tools")
    tools.add_argument("--group", default=None)
    tools.add_argument("--status", default=None, choices=["planned", "active", "disabled"])

    import_cgm = sub.add_parser("import-cgm", help="Import and normalize CGM CSV/JSON data")
    import_cgm.add_argument("--file", required=True, help="Path to a CGM CSV or JSON file")
    import_cgm.add_argument("--format", required=True, choices=["csv", "json"])
    import_cgm.add_argument("--user-id", required=True)
    import_cgm.add_argument("--timezone", default="Asia/Shanghai")
    import_cgm.add_argument("--source", default=None)

    tool_call = sub.add_parser("tool-call", help="Call an active or planned CGM tool with a JSON input file")
    tool_call.add_argument("tool_name")
    tool_call.add_argument("--input", required=True, help="JSON file containing tool arguments")
    tool_call.add_argument("--session-id", required=True)

    hermes_install = sub.add_parser("hermes-install", help="Install or refresh Hermes user-plugin integration")
    hermes_install.add_argument("--project-root", default=None)
    hermes_install.add_argument("--hermes-home", default=None)
    hermes_install.add_argument("--hermes-bin", default=None)
    hermes_install.add_argument("--skip-editable-install", action="store_true")
    hermes_install.add_argument("--skip-runtime-config", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = AppConfig.from_env()

    if args.command == "status":
        status = _hermes_status(config)
        print(f"project: hermes-cgm-agent")
        print(f"hermes_available: {str(status['available']).lower()}")
        print(f"hermes_executable: {status['executable']}")
        print(f"hermes_version: {status['version'] or ''}")
        print(f"database_path: {config.database_path}")
        if status["detail"] and status["detail"] != status["version"]:
            print(f"detail: {status['detail']}")
        return 0 if status["available"] else 1

    if args.command == "dev-status":
        status = _hermes_status(config)
        registry = build_default_tool_registry()
        tools = registry.list()
        planned_tools = [tool for tool in tools if tool.status == "planned"]
        active_tools = [tool for tool in tools if tool.status == "active"]
        store = SQLiteStore(config.database_path)
        store.initialize()
        cgm_repository = SQLiteCGMRepository(store)
        cgm_status = cgm_repository.status()
        with store.connect() as conn:
            report_table = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'reports'
                """
            ).fetchone()
            report_count = conn.execute("SELECT COUNT(*) AS count FROM reports").fetchone()
            memory_tables = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name IN ('l1_episodes', 'l2_profile_items', 'l3_hypotheses', 'memory_candidates')
                """
            ).fetchall()
        memory_present = len(memory_tables) == 4

        print("project: hermes-cgm-agent")
        print("architecture: Hermes-native plugins + CGM capability layer")
        print("main_shell: Hermes runtime")
        print("support_surfaces: local CLI for import/tool/install only")
        print("ui_mainline: false")
        print(f"hermes_available: {str(status['available']).lower()}")
        print(f"hermes_version: {status['version'] or ''}")
        print(f"database_path: {config.database_path}")
        print(f"tool_count: {len(tools)}")
        print(f"planned_tool_count: {len(planned_tools)}")
        print(f"active_tool_count: {len(active_tools)}")
        print(f"domain_model_count: {len(DOMAIN_MODELS)}")
        print(f"domain_models: {', '.join(DOMAIN_MODELS)}")
        print(f"cgm_repository_tables_present: {str(cgm_status.tables_present).lower()}")
        print(f"cgm_repository_table_count: {cgm_status.table_count}")
        print(f"glucose_point_count: {cgm_status.glucose_point_count}")
        print(f"import_batch_count: {cgm_status.import_batch_count}")
        print(f"user_event_count: {cgm_status.user_event_count}")
        print("cgm_importer_present: true")
        print("cgm_importer_formats: csv,json")
        print("cgm_normalizer_present: true")
        print("cgm_analytics_present: true")
        print("cgm_analytics_metrics: TIR,TAR,TBR,MBG,CV,GMI,LBGI,HBGI,data_coverage")
        print("cgm_event_tools_present: true")
        print("glucose_event_detection_present: true")
        print(f"cgm_reports_present: {str(report_table is not None).lower()}")
        print(f"report_count: {int(report_count['count'] if report_count else 0)}")
        print(f"memory_tables_present: {str(memory_present).lower()}")
        print("memory_layers: L0_context,L1_episode,L2_profile,L3_hypothesis")
        print("memory_retrieval: hybrid_bm25_dense_rrf")
        print("dual_track_rag_present: true")
        print("current_phase: G8 memory/rag implemented")
        print("prototype_limit: L2->USER.md sync and live Hermes provider install are spikes")
        print("test_command: PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests")
        return 0 if status["available"] else 1

    if args.command == "hermes-version":
        status = _hermes_status(config)
        if status["detail"]:
            print(status["detail"])
        return 0 if status["available"] else 1

    if args.command == "tools":
        registry = build_default_tool_registry()
        for spec in registry.list(group=args.group, status=args.status):
            print(
                f"{spec.name}\tgroup={spec.group}\tstatus={spec.status}\t"
                f"risk={spec.risk_level}\taudit={str(spec.writes_audit).lower()}"
            )
        return 0

    if args.command == "import-cgm":
        return _import_cgm(
            db_path=config.database_path,
            file_path=Path(args.file),
            source_format=args.format,
            user_id=args.user_id,
            timezone_name=args.timezone,
            source=args.source,
        )

    if args.command == "tool-call":
        return _tool_call(
            db_path=config.database_path,
            tool_name=args.tool_name,
            input_path=Path(args.input),
            session_id=args.session_id,
        )

    if args.command == "hermes-install":
        report = install_hermes_integration(
            project_root=Path(args.project_root) if args.project_root else None,
            hermes_home=Path(args.hermes_home) if args.hermes_home else None,
            hermes_bin=args.hermes_bin,
            install_editable=not args.skip_editable_install,
            configure_runtime=not args.skip_runtime_config,
        )
        print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0

    parser.error(f"Unhandled command {args.command}")
    return 2


def _hermes_status(config: AppConfig) -> dict[str, object]:
    hermes_bin = _resolve_hermes_bin(config.hermes_bin)
    try:
        completed = subprocess.run(
            [hermes_bin, "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": False,
            "executable": hermes_bin,
            "version": None,
            "detail": str(exc),
        }
    output = (completed.stdout or completed.stderr).strip()
    return {
        "available": completed.returncode == 0,
        "executable": hermes_bin,
        "version": output.splitlines()[0] if output else None,
        "detail": output or None,
    }


def _resolve_hermes_bin(configured: str | None) -> str:
    if configured:
        return configured
    discovered = shutil.which("hermes")
    if discovered:
        return discovered
    fallback = default_hermes_exe()
    return str(fallback) if fallback is not None else "hermes"


def _import_cgm(
    *,
    db_path: Path,
    file_path: Path,
    source_format: str,
    user_id: str,
    timezone_name: str,
    source: str | None,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    repository = SQLiteCGMRepository(store)
    importer = CGMImporter()

    if source_format == "csv":
        batch = importer.import_csv(file_path)
    elif source_format == "json":
        batch = importer.import_json(file_path)
    else:
        raise ValueError(f"Unsupported import format: {source_format}")

    normalizer = CGMNormalizer()
    normalized = normalizer.normalize_batch(
        batch,
        NormalizationConfig(
            user_id=user_id,
            source=source or f"{source_format}:{file_path.stem}",
            default_timezone=timezone_name,
        ),
    )
    stored_batch = batch.model_copy(
        update={"issues": [*batch.issues, *normalized.issues]}
    )
    repository.create_import_batch(stored_batch)

    inserted_count = 0
    duplicate_count = 0
    for point in normalized.points:
        try:
            repository.create_glucose_point(point)
            inserted_count += 1
        except sqlite3.IntegrityError:
            duplicate_count += 1

    payload = {
        "status": "ok",
        "batch_id": stored_batch.batch_id,
        "source_name": stored_batch.source_name,
        "source_format": stored_batch.source_format,
        "raw_record_count": stored_batch.record_count,
        "import_issue_count": stored_batch.issue_count,
        "normalized_point_count": len(normalized.points),
        "inserted_point_count": inserted_count,
        "duplicate_point_count": duplicate_count + normalized.duplicate_count,
        "missing_range_count": len(normalized.missing_ranges),
        "database_path": str(db_path),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _tool_call(
    *,
    db_path: Path,
    tool_name: str,
    input_path: Path,
    session_id: str,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    arguments = _read_json_object(input_path)
    executor = ToolExecutor(
        repository=SQLiteCGMRepository(store),
        audit_service=AuditService(store),
    )
    response = executor.execute(
        tool_name=tool_name,
        arguments=arguments,
        session_id=session_id,
    )
    body = response.to_dict()
    print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    return 0 if response.status == "ok" else 1


def _read_json_object(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("tool-call input must be a JSON object")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
