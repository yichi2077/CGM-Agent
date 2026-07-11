from __future__ import annotations

import argparse

from hermes_cgm_agent.config import default_timezone, default_user_id


DOMAIN_MODELS: list[str] = []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cgm-agent", description="Hermes-backed personal CGM agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show Hermes and local data-store status")
    sub.add_parser("hermes-version", help="Print Hermes version details")
    tools = sub.add_parser("tools", help="List Hermes-facing CGM tools")
    tools.add_argument("--group", default=None)
    tools.add_argument("--status", default=None, choices=["planned", "active", "disabled"])
    imp = sub.add_parser("import-cgm", help="Import CGM CSV or JSON data")
    imp.add_argument("--file", required=True); imp.add_argument("--format", required=True, choices=["csv", "json"]); imp.add_argument("--user-id", required=True); imp.add_argument("--timezone", default=default_timezone()); imp.add_argument("--source", default=None)
    auth = sub.add_parser("dexcom-auth", help="Authorize Dexcom API access")
    auth.add_argument("--user-id", required=True); auth.add_argument("--state", default=None); auth.add_argument("--code", default=None)
    sync = sub.add_parser("dexcom-sync", help="Sync Dexcom readings into local storage")
    sync.add_argument("--user-id", required=True); sync.add_argument("--days", type=int, default=7); sync.add_argument("--force", action="store_true"); sync.add_argument("--session-id", default="dexcom-cli-session")
    poll = sub.add_parser("source-poll", help="Poll a supported CGM source once")
    poll.add_argument("--user-id", required=True); poll.add_argument("--kind", required=True, choices=["xdrip", "juggluco", "nightscout"]); poll.add_argument("--url", required=True); poll.add_argument("--count", type=int, default=12); poll.add_argument("--source", default=None); poll.add_argument("--db-path", default=None); poll.add_argument("--expected-interval-min", type=int, default=5)
    push = sub.add_parser("push-tick", help="Run the idempotent push scheduler once")
    push.add_argument("--user-id", default=default_user_id()); push.add_argument("--now", default=None); push.add_argument("--timezone", default=default_timezone()); push.add_argument("--db-path", default=None)
    install = sub.add_parser("hermes-install", help="Install or refresh Hermes integration")
    install.add_argument("--project-root", default=None); install.add_argument("--hermes-home", default=None); install.add_argument("--hermes-bin", default=None); install.add_argument("--skip-editable-install", action="store_true"); install.add_argument("--skip-runtime-config", action="store_true"); install.add_argument("--dry-run", action="store_true")
    migrate = sub.add_parser("migrate-db", help="Migrate the legacy local store")
    migrate.add_argument("--dry-run", action="store_true"); migrate.add_argument("--force", action="store_true")
    return parser
