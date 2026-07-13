from __future__ import annotations

import json
import sys
from pathlib import Path

from hermes_cgm_agent.config import AppConfig, default_user_id, default_timezone
from hermes_cgm_agent.hermes_plugins import install_hermes_integration
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.tools import build_default_tool_registry
from hermes_cgm_agent.storage.sqlite import SQLiteStore

from hermes_cgm_agent.cli.parser import build_parser, DOMAIN_MODELS
from hermes_cgm_agent.cli.status import _warn_legacy_store_if_relevant, _hermes_status
from hermes_cgm_agent.cli.data import _import_cgm, _tool_call
from hermes_cgm_agent.cli.dexcom import _dexcom_auth, _dexcom_sync
from hermes_cgm_agent.cli.aidex import _aidex_auth, _aidex_status, _aidex_sync
from hermes_cgm_agent.cli.source import _source_poll
from hermes_cgm_agent.cli.bridge import _bridge_poll, _bridge_status
from hermes_cgm_agent.cli.simulation import _simulate
from hermes_cgm_agent.cli.acceptance import _hermes_accept
from hermes_cgm_agent.cli.memory import _memory_synthesize, _context_build, _seed_demo, _default_demo_csv
from hermes_cgm_agent.cli.push import _push_tick
from hermes_cgm_agent.cli.kb import (
    _kb_ingest,
    _kb_ingest_llm,
    _kb_ingest_batch,
    _kb_merge,
    _kb_pending,
    _kb_approve_cli,
    _eval_rag,
)


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    if effective_argv and effective_argv[0].startswith("aidex-"):
        from hermes_cgm_agent.services.aidex import load_aidex_environment

        load_aidex_environment()
    if effective_argv and effective_argv[0].startswith("bridge-"):
        from hermes_cgm_agent.services.sources import load_bridge_environment

        load_bridge_environment()
    parser = build_parser()
    args = parser.parse_args(effective_argv)
    config = AppConfig.from_env()
    _warn_legacy_store_if_relevant(config)

    if args.command == "status":
        status = _hermes_status(config)
        print("project: hermes-cgm-agent")
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
        print(f"import_batch_count: {cgm_status.import_batch_count}")
        print(f"user_event_count: {cgm_status.user_event_count}")
        print(f"detected_glucose_event_count: {cgm_status.detected_glucose_event_count}")
        print("cgm_importer_present: true")
        print("cgm_importer_formats: csv,json")
        print("cgm_normalizer_present: true")
        print("cgm_source_collector_present: true")
        print("cgm_source_collector_kinds: juggluco,xdrip,nightscout,aidex_api")
        print("android_bridge_production_path_present: true")
        print("bridge_health_preflight_present: true")
        print("aidex_official_api_present: true")
        print("cgm_analytics_present: true")
        print("cgm_analytics_metrics: TIR,TAR,TBR,MBG,CV,GMI,LBGI,HBGI,data_coverage")
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
        print("current_phase: Android Juggluco bridge implemented; real phone validation pending")
        print("prototype_limit: exact sensor/phone pairing, LAN endpoint validation, authoritative KB verification, and email delivery remain workflow-dependent")
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

    if args.command == "source-poll":
        return _source_poll(
            db_path=Path(args.db_path) if args.db_path else config.database_path,
            user_id=args.user_id,
            kind=args.kind,
            url=args.url,
            count=args.count,
            source=args.source,
            expected_interval_minutes=args.expected_interval_min,
        )

    if args.command == "simulate":
        return _simulate(
            csv_path=Path(args.csv),
            user_id=args.user_id,
            source_label=args.source,
            timezone_name=args.timezone,
            db_path=Path(args.db_path) if args.db_path else None,
            acceleration=1.0 if args.realtime else args.acceleration,
            max_speed=args.max_speed,
            time_base=args.time_base,
            days=args.days,
            expected_interval_minutes=args.expected_interval_min,
            out_dir=Path(args.out_dir) if args.out_dir else None,
            hermes=args.hermes,
            fail_fast=args.fail_fast,
        )

    if args.command == "bridge-status":
        return _bridge_status(db_path=config.database_path)

    if args.command == "bridge-poll":
        return _bridge_poll(db_path=config.database_path)

    if args.command == "aidex-auth":
        return _aidex_auth(
            db_path=config.database_path,
            user_id=args.user_id,
            state=args.state,
            code=args.code,
        )

    if args.command == "aidex-sync":
        return _aidex_sync(
            db_path=config.database_path,
            user_id=args.user_id,
            days=args.days,
            force=args.force,
            incremental=args.incremental,
            overlap_minutes=args.overlap_minutes,
            bootstrap_hours=args.bootstrap_hours,
        )

    if args.command == "aidex-status":
        return _aidex_status(
            db_path=config.database_path,
            user_id=args.user_id,
            live=args.live,
        )

    if args.command == "hermes-accept":
        return _hermes_accept(
            source_db=Path(args.source_db) if args.source_db else config.database_path,
            user_id=args.user_id,
            duration_hours=args.duration_hours,
            output_dir=args.output_dir,
            timezone_name=args.timezone,
            hermes_home=args.hermes_home,
            hermes_bin=args.hermes_bin,
            provider=args.provider,
            provider_user_agent=args.provider_user_agent,
            provider_max_tokens=args.provider_max_tokens,
            model=args.model,
            deliver=args.deliver,
            max_model_calls=args.max_model_calls,
            max_external_messages=args.max_external_messages,
            activate_on_pass=args.activate_on_pass,
            run_model=not args.no_model,
            send_external=args.send_external,
            timeout_seconds=args.timeout_seconds,
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

    if args.command == "migrate-db":
        from hermes_cgm_agent.migrate import OK_STATUSES, migrate

        result = migrate(dry_run=args.dry_run, force=args.force)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in OK_STATUSES else 1

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

    if args.command == "kb-pending":
        return _kb_pending(
            kb_path=Path(args.kb) if args.kb else None,
            output_format=args.format,
            limit=args.limit,
        )

    if args.command == "kb-approve":
        return _kb_approve_cli(
            kb_path=Path(args.kb) if args.kb else None,
            card_id=args.card_id,
            reviewer=args.reviewer,
            reviewed_at=args.reviewed_at,
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
            dry_run=args.dry_run,
        )
        print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
        if getattr(args, "seed_demo", False):
            print("[cgm-agent] seeding demo CGM data into the canonical store...", file=sys.stderr)
            return _seed_demo(
                db_path=config.database_path,
                csv_path=_default_demo_csv(),
                user_id=default_user_id(),
                timezone_name=default_timezone(),
                query="最近血糖怎么样",
            )
        return 0

    parser.error(f"Unhandled command {args.command}")
    return 2
