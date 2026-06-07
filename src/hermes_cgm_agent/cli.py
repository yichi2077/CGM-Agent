from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from hermes_cgm_agent.domain import (
    DataScope,
    DeviceSession,
    EvidenceRef,
    GlucoseAggregate,
    GlucoseEvent,
    GlucosePoint,
    L1Episode,
    RawCGMRecord,
    RawImportBatch,
    Report,
    UserEvent,
)
from hermes_cgm_agent.hermes_plugins import install_hermes_integration
from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.analytics import CGMAnalyticsService, GlucoseEventDetector
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import (
    CGMImporter,
    CGMNormalizer,
    NormalizationConfig,
    SQLiteCGMRepository,
)
from hermes_cgm_agent.services.memory import (
    ConsolidationService,
    L0ContextBuilder,
    MemoryContextAssembler,
    SQLiteMemoryRepository,
)
from hermes_cgm_agent.services.memory.user_md_sync import render_l2_user_md_block
from hermes_cgm_agent.services.scheduling import (
    PushSchedulerConfig,
    PushSchedulerService,
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

    dexcom_auth = sub.add_parser(
        "dexcom-auth",
        help="Authorize Dexcom API v3 access (OAuth2) and store encrypted tokens",
    )
    dexcom_auth.add_argument("--user-id", required=True)
    dexcom_auth.add_argument("--state", default=None, help="Optional OAuth state value")
    dexcom_auth.add_argument(
        "--code",
        default=None,
        help="Authorization code or full redirect URL (skips the interactive prompt)",
    )

    dexcom_sync = sub.add_parser(
        "dexcom-sync",
        help="Sync glucose readings and events from the Dexcom cloud into local storage",
    )
    dexcom_sync.add_argument("--user-id", required=True)
    dexcom_sync.add_argument("--days", type=int, default=7)
    dexcom_sync.add_argument("--force", action="store_true")
    dexcom_sync.add_argument("--session-id", default="dexcom-cli-session")

    synthesize = sub.add_parser(
        "memory-synthesize",
        help="Generate a Warm memory summary from the current CGM window and memory state",
    )
    synthesize.add_argument("--user-id", required=True)
    synthesize.add_argument("--window-start", required=True, help="ISO 8601 datetime")
    synthesize.add_argument("--window-end", required=True, help="ISO 8601 datetime")
    synthesize.add_argument("--period", choices=["daily", "weekly", "monthly"], default="weekly")

    context_build = sub.add_parser(
        "context-build",
        help="Build the deterministic L0 working-memory context as JSON",
    )
    context_build.add_argument("--user-id", required=True)
    context_build.add_argument("--anchor-at", default=None, help="ISO 8601 datetime")
    context_build.add_argument("--source", default=None)

    seed_demo = sub.add_parser(
        "seed-demo",
        help=(
            "Run the full data->memory->recall chain on a CGM CSV: import points, "
            "derive L1 episodes from detected glucose events (real per-day facts), "
            "consolidate to L2/L3, synthesize a warm summary, and show recall. "
            "Populates the DB so dev-status is non-empty."
        ),
    )
    seed_demo.add_argument(
        "--csv",
        default=None,
        help="CGM CSV path (default: examples/cgm_test_dataset/cgm_3x14.csv)",
    )
    seed_demo.add_argument("--user-id", default="demo-user")
    seed_demo.add_argument("--timezone", default="Asia/Shanghai")
    seed_demo.add_argument(
        "--db-path",
        default=None,
        help="SQLite DB path (default: the configured runtime DB)",
    )
    seed_demo.add_argument(
        "--query",
        default="最近的血糖模式 recent overnight low hyper pattern",
        help="Recall query used to demonstrate memory retrieval",
    )

    push_tick = sub.add_parser(
        "push-tick",
        help=(
            "Tiered-push scheduler tick (cron-callable): apply silent-consent, "
            "decide which of daily/weekly/monthly digests are due, and emit them "
            "idempotently. The project owns policy+content+state; Hermes/cron owns "
            "timing and delivery."
        ),
    )
    push_tick.add_argument("--user-id", default="demo-user")
    push_tick.add_argument("--now", default=None, help="ISO 8601 datetime override (testing)")
    push_tick.add_argument("--timezone", default="Asia/Shanghai")
    push_tick.add_argument("--db-path", default=None, help="SQLite DB path (default: runtime DB)")

    sub.add_parser(
        "kb-validate",
        help="Validate the authoritative knowledge base (structure + verified sign-off provenance)",
    )

    kb_ingest = sub.add_parser(
        "kb-ingest",
        help="Extract candidate claim cards from a PDF into a review queue",
    )
    kb_ingest.add_argument("--pdf", required=True, help="Path to a source PDF")
    kb_ingest.add_argument("--out-dir", required=True, help="Directory for candidate JSON and review markdown")
    kb_ingest.add_argument("--kb-version", default="kb-candidate")

    kb_ingest_llm = sub.add_parser(
        "kb-ingest-llm",
        help="Extract claim cards via Hermes CLI (text + vision) into a review queue",
    )
    kb_ingest_llm.add_argument("--pdf", required=True, help="Path to a source PDF")
    kb_ingest_llm.add_argument("--out-dir", required=True, help="Directory for candidate JSON and review markdown")
    kb_ingest_llm.add_argument("--kb-version", default="kb-2026-06-auto-v1")
    kb_ingest_llm.add_argument("--pages", default=None, help="Optional page range, e.g. 1-10,15")
    kb_ingest_llm.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "text", "vision", "hybrid"],
        help="Page extraction routing mode",
    )
    kb_ingest_llm.add_argument(
        "--engine",
        default="hermes",
        choices=["hermes", "sentence"],
        help="Extraction engine: Hermes LLM or deterministic sentence heuristic",
    )

    kb_ingest_batch = sub.add_parser(
        "kb-ingest-batch",
        help="Batch ingest PDFs from pdf_manifest.json",
    )
    kb_ingest_batch.add_argument("--out-dir", required=True)
    kb_ingest_batch.add_argument("--kb-version", default="kb-2026-06-auto-v1")
    kb_ingest_batch.add_argument("--priority-min", type=int, default=1)
    kb_ingest_batch.add_argument(
        "--engine",
        default="sentence",
        choices=["hermes", "sentence"],
        help="Default sentence engine is offline-safe; use hermes when available",
    )
    kb_ingest_batch.add_argument("--mode", default="auto", choices=["auto", "text", "vision", "hybrid"])

    kb_merge = sub.add_parser(
        "kb-merge",
        help="Merge accepted candidate cards into authoritative_kb.json",
    )
    kb_merge.add_argument("--candidates", required=True, help="Candidate JSON file or directory")
    kb_merge.add_argument("--into", default=None, help="Target authoritative_kb.json path")
    kb_merge.add_argument("--dry-run", action="store_true")
    kb_merge.add_argument("--kb-version", default=None)

    eval_rag = sub.add_parser("eval-rag", help="Evaluate authoritative RAG hit@3")
    eval_rag.add_argument("--queries", default="eval/rag/queries.jsonl")
    eval_rag.add_argument("--kb", default=None)
    eval_rag.add_argument(
        "--min-hit3",
        type=float,
        default=None,
        help="Fail (exit 1) if hit@3 is below this threshold, e.g. 0.95 (CI gate)",
    )

    hermes_install = sub.add_parser("hermes-install", help="Install or refresh Hermes user-plugin integration")
    hermes_install.add_argument("--project-root", default=None)
    hermes_install.add_argument("--hermes-home", default=None)
    hermes_install.add_argument("--hermes-bin", default=None)
    hermes_install.add_argument("--skip-editable-install", action="store_true")
    hermes_install.add_argument("--skip-runtime-config", action="store_true")
    hermes_install.add_argument("--smoke", action="store_true")
    hermes_install.add_argument("--dry-run", action="store_true")

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
            consolidation_row = conn.execute(
                """
                SELECT payload_json, created_at
                FROM audit_logs
                WHERE event_type = 'memory_consolidation'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        memory_present = len(memory_tables) == 4
        consolidation_payload = (
            store.unseal(consolidation_row["payload_json"], legacy="json")
            if consolidation_row
            else None
        )

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
        onboarding_status = "ready" if cgm_status.glucose_point_count > 0 else "needs_data"
        print(f"onboarding_status: {onboarding_status}")
        if onboarding_status == "needs_data":
            print(
                "recommended_next_command: "
                f"PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 "
                f"-m hermes_cgm_agent seed-demo --db-path {config.database_path}"
            )
        print(f"import_batch_count: {cgm_status.import_batch_count}")
        print(f"user_event_count: {cgm_status.user_event_count}")
        print("cgm_importer_present: true")
        print("cgm_importer_formats: csv,json")
        print("cgm_normalizer_present: true")
        print("cgm_analytics_present: true")
        print("cgm_analytics_metrics: TIR,TAR,TBR,MBG,CV,GMI,LBGI,HBGI,MODD,CONGA1,CONGA2,CONGA4,data_coverage")
        print("cgm_event_tools_present: true")
        print("glucose_event_detection_present: true")
        print(f"cgm_reports_present: {str(report_table is not None).lower()}")
        print(f"report_count: {int(report_count['count'] if report_count else 0)}")
        print(f"memory_tables_present: {str(memory_present).lower()}")
        print("memory_layers: L0_context,L1_episode,L2_profile,L3_hypothesis")
        print("l0_context_builder_present: true")
        print("memory_retrieval: hot_sql_direct + warm_summary + authoritative_bm25 + personal_l1_hybrid_threshold")
        print("l2_user_md_sync_present: true")
        print(f"memory_last_consolidation_at: {consolidation_row['created_at'] if consolidation_row else ''}")
        print(
            "memory_last_consolidation_profiles_updated: "
            f"{consolidation_payload.get('profiles_updated', '') if consolidation_payload else ''}"
        )
        print(
            "memory_last_consolidation_hypotheses_updated: "
            f"{consolidation_payload.get('hypotheses_updated', '') if consolidation_payload else ''}"
        )
        print("dual_track_rag_present: true")
        print("push_scheduler_present: true")
        print("push_tiers: daily,weekly,monthly")
        print("silent_consent_present: true")
        print("current_phase: tiered-push product loop implemented")
        print("prototype_limit: authoritative KB verification and the external delivery channel (email/webhook timing) remain workflow-dependent")
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

    if args.command == "dexcom-auth":
        return _dexcom_auth(
            db_path=config.database_path,
            user_id=args.user_id,
            state=args.state,
            code=args.code,
        )

    if args.command == "dexcom-sync":
        return _dexcom_sync(
            db_path=config.database_path,
            user_id=args.user_id,
            days=args.days,
            force=args.force,
            session_id=args.session_id,
        )

    if args.command == "memory-synthesize":
        return _memory_synthesize(
            db_path=config.database_path,
            user_id=args.user_id,
            window_start=args.window_start,
            window_end=args.window_end,
            period=args.period,
        )

    if args.command == "context-build":
        return _context_build(
            db_path=config.database_path,
            user_id=args.user_id,
            anchor_at=args.anchor_at,
            source=args.source,
        )

    if args.command == "seed-demo":
        return _seed_demo(
            db_path=Path(args.db_path) if args.db_path else config.database_path,
            csv_path=Path(args.csv) if args.csv else _default_demo_csv(),
            user_id=args.user_id,
            timezone_name=args.timezone,
            query=args.query,
        )

    if args.command == "push-tick":
        return _push_tick(
            db_path=Path(args.db_path) if args.db_path else config.database_path,
            user_id=args.user_id,
            now=args.now,
            timezone_name=args.timezone,
        )

    if args.command == "kb-validate":
        from hermes_cgm_agent.services.rag import validate_knowledge_base

        problems = validate_knowledge_base()
        if problems:
            print(json.dumps({"valid": False, "problems": problems}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"valid": True, "problems": []}, ensure_ascii=False))
        return 0

    if args.command == "kb-ingest":
        return _kb_ingest(
            pdf_path=Path(args.pdf),
            out_dir=Path(args.out_dir),
            kb_version=args.kb_version,
        )

    if args.command == "kb-ingest-llm":
        return _kb_ingest_llm(
            config=config,
            pdf_path=Path(args.pdf),
            out_dir=Path(args.out_dir),
            kb_version=args.kb_version,
            pages=args.pages,
            mode=args.mode,
            engine=args.engine,
        )

    if args.command == "kb-ingest-batch":
        return _kb_ingest_batch(
            config=config,
            out_dir=Path(args.out_dir),
            kb_version=args.kb_version,
            priority_min=args.priority_min,
            mode=args.mode,
            engine=args.engine,
        )

    if args.command == "kb-merge":
        return _kb_merge(
            candidates_path=Path(args.candidates),
            into_path=Path(args.into) if args.into else None,
            dry_run=args.dry_run,
            kb_version=args.kb_version,
        )

    if args.command == "eval-rag":
        return _eval_rag(
            queries_path=Path(args.queries),
            kb_path=Path(args.kb) if args.kb else None,
            min_hit3=args.min_hit3,
        )

    if args.command == "hermes-install":
        report = install_hermes_integration(
            project_root=Path(args.project_root) if args.project_root else None,
            hermes_home=Path(args.hermes_home) if args.hermes_home else None,
            hermes_bin=args.hermes_bin,
            install_editable=not args.skip_editable_install,
            configure_runtime=not args.skip_runtime_config,
            smoke=args.smoke,
            dry_run=args.dry_run,
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


def _dexcom_auth(
    *,
    db_path: Path,
    user_id: str,
    state: str | None,
    code: str | None,
) -> int:
    from hermes_cgm_agent.services.dexcom import (
        DexcomAuthError,
        DexcomAuthService,
        DexcomClient,
        DexcomConfig,
        DexcomTokenStore,
    )

    try:
        dexcom_config = DexcomConfig.from_env()
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1

    store = SQLiteStore(db_path)
    store.initialize()
    client = DexcomClient(dexcom_config)
    auth = DexcomAuthService(
        config=dexcom_config,
        client=client,
        token_store=DexcomTokenStore(store),
    )

    authorize_url = auth.authorization_url(state=state)
    print(f"environment: {dexcom_config.environment}")
    print(f"redirect_uri: {dexcom_config.redirect_uri}")
    print("Open this URL in a browser, authorize, then copy the redirect URL you land on:")
    print(authorize_url)

    code_or_url = code
    if not code_or_url:
        try:
            code_or_url = input("Paste the redirect URL or authorization code: ").strip()
        except EOFError:
            code_or_url = ""

    try:
        token = auth.complete_authorization(user_id, code_or_url)
    except (DexcomAuthError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1

    payload = {
        "status": "ok",
        "user_id": token.user_id,
        "environment": token.environment,
        "scope": token.scope,
        "token_type": token.token_type,
        "expires_at": token.expires_at.isoformat(),
        "database_path": str(db_path),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _dexcom_sync(
    *,
    db_path: Path,
    user_id: str,
    days: int,
    force: bool,
    session_id: str,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    executor = ToolExecutor(
        repository=SQLiteCGMRepository(store),
        audit_service=AuditService(store),
    )
    response = executor.execute(
        tool_name="data.dexcom_sync",
        arguments={"user_id": user_id, "days": days, "force": force},
        session_id=session_id,
    )
    body = response.to_dict()
    body["database_path"] = str(db_path)
    print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    return 0 if response.status == "ok" else 1


def _memory_synthesize(
    *,
    db_path: Path,
    user_id: str,
    window_start: str,
    window_end: str,
    period: str,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    repository = SQLiteCGMRepository(store)
    memory_repository = SQLiteMemoryRepository(store)
    scope = DataScope(
        user_id=user_id,
        window_start=_parse_iso_datetime(window_start),
        window_end=_parse_iso_datetime(window_end),
    )
    aggregate = CGMAnalyticsService().compute_aggregate(
        points=repository.list_glucose_points(scope),
        scope=scope,
        window_label=_period_to_window_label(period),
    )
    summary = ConsolidationService(repository=memory_repository).synthesize_state(
        user_id=user_id,
        window_start=scope.window_start,
        window_end=scope.window_end,
        period=period,
        metrics_summary={
            "tir_pct": aggregate.tir,
            "mean_mgdl": aggregate.mbg,
        },
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "summary_id": summary.summary_id,
                "user_id": summary.user_id,
                "period": summary.period,
                "window_start": summary.window_start.isoformat(),
                "window_end": summary.window_end.isoformat(),
                "content": summary.content,
                "metrics": summary.metrics,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _context_build(
    *,
    db_path: Path,
    user_id: str,
    anchor_at: str | None,
    source: str | None,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    context = L0ContextBuilder(
        repository=SQLiteCGMRepository(store),
    ).build(
        user_id=user_id,
        anchor_at=_parse_iso_datetime(anchor_at) if anchor_at else None,
        source=source,
    )
    print(json.dumps(context.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return 0


def _default_demo_csv() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "cgm_test_dataset"
        / "cgm_3x14.csv"
    )


def _episodes_from_detected_events(
    events: list[GlucoseEvent], *, now: datetime
) -> list[L1Episode]:
    """Derive L1 episodes from DETECTED glucose events (P1 demo seeding).

    Detected hypo/hyper/overnight-low events are deterministic FACTS about the
    data (not agent inferences), each carrying a real per-day timestamp — so they
    are a faithful, multi-day data-driven source for the memory chain. occurred_at
    is the event's real start time (NOT processing time), so consolidation groups
    them across the actual calendar days the patterns occurred. This is CLI-local
    demo orchestration; it does not change the production confirmation-gated
    memory path (D026).
    """
    episodes: list[L1Episode] = []
    for event in events:
        episodes.append(
            L1Episode(
                episode_id=f"evt-{event.event_id}",
                user_id=event.user_id,
                occurred_at=event.ts_start,
                episode_type=getattr(event.event_type, "value", event.event_type),
                summary=event.summary,
                evidence_refs=event.evidence_refs,
                confidence=0.9,
                created_at=now,
                last_referenced_at=now,
            )
        )
    return episodes


def _seed_demo(
    *,
    db_path: Path,
    csv_path: Path,
    user_id: str,
    timezone_name: str,
    query: str,
) -> int:
    if not csv_path.exists():
        print(
            json.dumps(
                {"status": "error", "message": f"CSV not found: {csv_path}"},
                ensure_ascii=False,
            )
        )
        return 1

    store = SQLiteStore(db_path)
    store.initialize()
    repository = SQLiteCGMRepository(store)
    memory_repository = SQLiteMemoryRepository(store)

    # 1. import + normalize the CGM CSV into storage
    batch = CGMImporter().import_csv(csv_path)
    normalized = CGMNormalizer().normalize_batch(
        batch,
        NormalizationConfig(
            user_id=user_id,
            source=f"seed-demo:{csv_path.stem}",
            default_timezone=timezone_name,
        ),
    )
    repository.create_import_batch(
        batch.model_copy(update={"issues": [*batch.issues, *normalized.issues]})
    )
    inserted = 0
    duplicate = 0
    for point in normalized.points:
        try:
            repository.create_glucose_point(point)
            inserted += 1
        except sqlite3.IntegrityError:
            duplicate += 1

    if not normalized.points:
        print(json.dumps({"status": "error", "message": "no valid points imported"}))
        return 1

    window_start = min(point.timestamp for point in normalized.points)
    window_end = max(point.timestamp for point in normalized.points) + timedelta(minutes=5)
    scope = DataScope(user_id=user_id, window_start=window_start, window_end=window_end)
    stored_points = repository.list_glucose_points(scope)

    # 2. analytics over the full window
    aggregate = CGMAnalyticsService().compute_aggregate(
        points=stored_points, scope=scope, window_label="14d"
    )

    # 3. detect events -> L1 episodes dated by their real occurrence (data-driven memory)
    now = utc_now()
    events = GlucoseEventDetector().detect(points=stored_points, scope=scope)
    episodes = _episodes_from_detected_events(events, now=now)
    episode_inserted = 0
    for episode in episodes:
        try:
            memory_repository.create_episode(episode)
            episode_inserted += 1
        except sqlite3.IntegrityError:
            pass  # idempotent re-seed

    # 4. consolidate L1 -> L2 beliefs + L3 hypotheses (groups by distinct local day)
    consolidation = ConsolidationService(
        repository=memory_repository, audit_service=AuditService(store)
    )
    consolidation_report = consolidation.consolidate(user_id, now=now)

    # 5. synthesize a warm state summary (the "dreaming" digest used in prefetch)
    summary = consolidation.synthesize_state(
        user_id=user_id,
        window_start=window_start,
        window_end=window_end,
        period="weekly",
        metrics_summary={"tir_pct": aggregate.tir, "mean_mgdl": aggregate.mbg},
        now=now,
    )

    # 6. recall: assemble the personal-memory context for a query (prefetch core)
    recall = MemoryContextAssembler(repository=memory_repository).build_memory_context(
        user_id=user_id, query=query, top_k=5
    )
    profile_items = memory_repository.list_profile_items(user_id)

    # 7. show the USER.md L2 projection that would sync (no disk write here)
    user_md_preview = render_l2_user_md_block(profile_items)

    payload = {
        "status": "ok",
        "database_path": str(db_path),
        "data_chain": {
            "csv": str(csv_path),
            "points_inserted": inserted,
            "points_duplicate": duplicate,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "tir_pct": aggregate.tir,
            "mean_mgdl": aggregate.mbg,
            "detected_events": len(events),
        },
        "memory_chain": {
            "l1_episodes_created": episode_inserted,
            "l1_episode_total": len(memory_repository.list_episodes(user_id)),
            "l2_profiles_updated": consolidation_report.profiles_updated,
            "l3_hypotheses_updated": consolidation_report.hypotheses_updated,
            "l2_profile_total": len(profile_items),
            "l3_hypothesis_total": len(memory_repository.list_hypotheses(user_id)),
            "warm_summary_id": summary.summary_id,
            "warm_summary": summary.content,
        },
        "recall": {
            "query": query,
            "item_count": len(recall.items),
            "items": [
                {"layer": item["layer"], "summary": item["summary"]}
                for item in recall.items
            ],
            "missing_reason": recall.missing_reason,
        },
        "user_md_l2_preview": user_md_preview,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _push_tick(
    *,
    db_path: Path,
    user_id: str,
    now: str | None,
    timezone_name: str,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    service = PushSchedulerService(
        store=store,
        config=PushSchedulerConfig(timezone=timezone_name),
        audit_service=AuditService(store),
    )
    result = service.push_tick(
        user_id=user_id,
        now=_parse_iso_datetime(now) if now else None,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


def _default_pdf_dir() -> Path:
    return Path(__file__).resolve().parent / "knowledge" / "pdfs"


def _kb_ingest_llm(
    *,
    config: AppConfig,
    pdf_path: Path,
    out_dir: Path,
    kb_version: str,
    pages: str | None,
    mode: str,
    engine: str,
) -> int:
    from hermes_cgm_agent.knowledge.ingest import (
        HermesClaimExtractor,
        PageChunk,
        build_sentence_candidates,
        extract_pdf_text,
        filter_candidates,
        find_manifest_entry,
        load_pdf_pages,
        parse_page_range,
        write_candidate_json,
        write_quality_markdown,
        write_review_markdown,
    )
    from hermes_cgm_agent.knowledge.ingest.pipeline import IngestResult
    from hermes_cgm_agent.services.rag import load_knowledge_base

    manifest = find_manifest_entry(pdf_path)
    page_filter = parse_page_range(pages)
    image_dir = out_dir / "_page_images" / pdf_path.stem
    audits: list[dict[str, object]] = []

    if engine == "sentence":
        page_texts = extract_pdf_text(pdf_path)
        if page_filter is not None:
            page_texts = [item for item in page_texts if item[0] in page_filter]
        result = build_sentence_candidates(
            source_path=pdf_path,
            pages=page_texts,
            kb_version=kb_version,
            citation=manifest.citation,
            doc_title=manifest.doc_title,
            population=manifest.default_population,
        )
        raw_candidates = result.candidates
        pages_by_no = {
            page_no: PageChunk(page_no=page_no, text=text, extraction_mode="text")
            for page_no, text in page_texts
        }
        chunks = []
    else:
        chunks = load_pdf_pages(
            pdf_path,
            manifest_entry=manifest,
            pages=page_filter,
            mode=mode,  # type: ignore[arg-type]
            image_dir=image_dir,
        )
        extractor = HermesClaimExtractor(
            hermes_exe=_resolve_hermes_bin(config.hermes_bin),
            timeout_seconds=config.timeout_seconds,
        )
        raw_candidates, extraction_audits = extractor.extract_cards(
            pdf_meta=manifest,
            pages=chunks,
            kb_version=kb_version,
        )
        audits = [
            {
                "page_no": item.page_no,
                "extraction_mode": item.extraction_mode,
                "status": item.status,
                "candidate_count": item.candidate_count,
                "error": item.error,
            }
            for item in extraction_audits
        ]
        pages_by_no = {chunk.page_no: chunk for chunk in chunks}

    quality = filter_candidates(
        raw_candidates,
        pages_by_no=pages_by_no,
        existing_cards=load_knowledge_base().cards,
    )
    ingest_result = IngestResult(
        source_path=str(pdf_path),
        page_count=len(chunks) if engine == "hermes" else len(page_texts),
        candidate_count=quality.accepted_count,
        candidates=quality.accepted,
    )

    base = pdf_path.stem
    json_path = out_dir / f"{base}.candidates.json"
    review_path = out_dir / f"{base}.review.md"
    quality_path = out_dir / f"{base}.quality.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_candidate_json(ingest_result, json_path)
    write_review_markdown(ingest_result, review_path)
    write_quality_markdown(quality, quality_path)
    print(
        json.dumps(
            {
                "status": "ok",
                "engine": engine,
                "source_path": str(pdf_path),
                "page_count": ingest_result.page_count,
                "raw_candidate_count": len(raw_candidates),
                "accepted_candidate_count": quality.accepted_count,
                "rejected_candidate_count": quality.rejected_count,
                "candidate_json": str(json_path),
                "review_markdown": str(review_path),
                "quality_markdown": str(quality_path),
                "audits": audits,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _kb_ingest_batch(
    *,
    config: AppConfig,
    out_dir: Path,
    kb_version: str,
    priority_min: int,
    mode: str,
    engine: str,
) -> int:
    from hermes_cgm_agent.knowledge.ingest import load_pdf_manifest

    pdf_dir = _default_pdf_dir()
    entries = [entry for entry in load_pdf_manifest() if entry.priority <= priority_min]
    results: list[dict[str, object]] = []
    for entry in entries:
        pdf_path = pdf_dir / entry.file_name
        if not pdf_path.exists():
            results.append({"file_name": entry.file_name, "status": "missing"})
            continue
        code = _kb_ingest_llm(
            config=config,
            pdf_path=pdf_path,
            out_dir=out_dir,
            kb_version=kb_version,
            pages=None,
            mode=mode,
            engine=engine,
        )
        results.append({"file_name": entry.file_name, "status": "ok" if code == 0 else "error"})
    print(json.dumps({"status": "ok", "processed": results}, ensure_ascii=False, indent=2))
    return 0


def _kb_merge(*, candidates_path: Path, into_path: Path | None, dry_run: bool, kb_version: str | None) -> int:
    from hermes_cgm_agent.knowledge.ingest import merge_candidates_into_kb

    files = (
        [candidates_path]
        if candidates_path.is_file()
        else sorted(candidates_path.glob("*.candidates.json"))
    )
    aggregate = {"added": [], "skipped": [], "total_after": 0, "kb_version": ""}
    target_kb = into_path
    for file_path in files:
        preview = merge_candidates_into_kb(
            candidates_path=file_path,
            kb_path=target_kb,
            dry_run=dry_run,
            kb_version=kb_version,
        )
        aggregate["added"].extend(preview.added)
        aggregate["skipped"].extend(preview.skipped)
        aggregate["total_after"] = preview.total_after
        aggregate["kb_version"] = preview.kb_version
        if not dry_run:
            from hermes_cgm_agent.knowledge.ingest.merge import DEFAULT_KB_PATH

            target_kb = into_path or DEFAULT_KB_PATH
    print(json.dumps({"status": "ok", "dry_run": dry_run, **aggregate}, ensure_ascii=False, indent=2))
    return 0


def _eval_rag(
    *,
    queries_path: Path,
    kb_path: Path | None,
    min_hit3: float | None = None,
    emit_report: bool = True,
) -> int:
    from hermes_cgm_agent.services.rag.eval_hit3 import evaluate_hit3

    report = evaluate_hit3(queries_path=queries_path, kb_path=kb_path)
    if min_hit3 is not None:
        report["min_hit3"] = min_hit3
        report["passed"] = report["hit_at_3"] >= min_hit3
    if emit_report:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if min_hit3 is not None and report["hit_at_3"] < min_hit3:
        return 1
    return 0


def _kb_ingest(*, pdf_path: Path, out_dir: Path, kb_version: str) -> int:
    from hermes_cgm_agent.knowledge.ingest import (
        build_candidate_cards,
        extract_pdf_text,
        write_candidate_json,
        write_review_markdown,
    )

    pages = extract_pdf_text(pdf_path)
    result = build_candidate_cards(
        source_path=pdf_path,
        pages=pages,
        kb_version=kb_version,
    )
    base = pdf_path.stem
    json_path = out_dir / f"{base}.candidates.json"
    review_path = out_dir / f"{base}.review.md"
    write_candidate_json(result, json_path)
    write_review_markdown(result, review_path)
    print(
        json.dumps(
            {
                "status": "ok",
                "source_path": str(pdf_path),
                "page_count": result.page_count,
                "candidate_count": result.candidate_count,
                "candidate_json": str(json_path),
                "review_markdown": str(review_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _read_json_object(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("tool-call input must be a JSON object")
    return payload


def _parse_iso_datetime(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO 8601 datetime: {raw}") from exc


def _period_to_window_label(period: str) -> str:
    return {
        "daily": "day",
        "weekly": "week",
        "monthly": "month",
    }.get(period, period)


if __name__ == "__main__":
    raise SystemExit(main())
