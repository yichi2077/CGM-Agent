from __future__ import annotations

import json
from pathlib import Path

from hermes_cgm_agent.cli.data import _import_cgm
from hermes_cgm_agent.cli.dexcom import _dexcom_auth, _dexcom_sync
from hermes_cgm_agent.cli.parser import build_parser
from hermes_cgm_agent.cli.push import _push_tick
from hermes_cgm_agent.cli.source import _source_poll
from hermes_cgm_agent.cli.status import _hermes_status, _warn_legacy_store_if_relevant
from hermes_cgm_agent.config import AppConfig
from hermes_cgm_agent.hermes_plugins import install_hermes_integration
from hermes_cgm_agent.services.tools import build_default_tool_registry


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AppConfig.from_env()
    _warn_legacy_store_if_relevant(config)

    if args.command in {"status", "hermes-version"}:
        status = _hermes_status(config)
        if args.command == "hermes-version":
            if status["detail"]:
                print(status["detail"])
        else:
            print("project: hermes-cgm-agent")
            print(f"hermes_available: {str(status['available']).lower()}")
            print(f"hermes_executable: {status['executable']}")
            print(f"hermes_version: {status['version'] or ''}")
            print(f"database_path: {config.database_path}")
            if status["detail"] and status["detail"] != status["version"]:
                print(f"detail: {status['detail']}")
        return 0 if status["available"] else 1

    if args.command == "tools":
        for spec in build_default_tool_registry().list(group=args.group, status=args.status):
            print(f"{spec.name}\tgroup={spec.group}\tstatus={spec.status}\trisk={spec.risk_level}\taudit={str(spec.writes_audit).lower()}")
        return 0

    if args.command == "import-cgm":
        return _import_cgm(db_path=config.database_path, file_path=Path(args.file), source_format=args.format, user_id=args.user_id, timezone_name=args.timezone, source=args.source)
    if args.command == "dexcom-auth":
        return _dexcom_auth(db_path=config.database_path, user_id=args.user_id, state=args.state, code=args.code)
    if args.command == "dexcom-sync":
        return _dexcom_sync(db_path=config.database_path, user_id=args.user_id, days=args.days, force=args.force, session_id=args.session_id)
    if args.command == "source-poll":
        return _source_poll(db_path=Path(args.db_path) if args.db_path else config.database_path, user_id=args.user_id, kind=args.kind, url=args.url, count=args.count, source=args.source, expected_interval_minutes=args.expected_interval_min)
    if args.command == "push-tick":
        return _push_tick(db_path=Path(args.db_path) if args.db_path else config.database_path, user_id=args.user_id, now=args.now, timezone_name=args.timezone)
    if args.command == "migrate-db":
        from hermes_cgm_agent.migrate import OK_STATUSES, migrate
        result = migrate(dry_run=args.dry_run, force=args.force)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in OK_STATUSES else 1
    if args.command == "hermes-install":
        report = install_hermes_integration(project_root=Path(args.project_root) if args.project_root else None, hermes_home=Path(args.hermes_home) if args.hermes_home else None, hermes_bin=args.hermes_bin, install_editable=not args.skip_editable_install, configure_runtime=not args.skip_runtime_config, dry_run=args.dry_run)
        print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    raise AssertionError(f"Unhandled command {args.command}")
