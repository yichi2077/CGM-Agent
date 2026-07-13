from __future__ import annotations

import argparse

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
from hermes_cgm_agent.config import default_user_id, default_timezone


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
    import_cgm.add_argument("--timezone", default=default_timezone())
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

    aidex_auth = sub.add_parser(
        "aidex-auth",
        help="Authorize the official MicroTech LinX/AiDEX API and store encrypted tokens",
    )
    aidex_auth.add_argument("--user-id", required=True)
    aidex_auth.add_argument("--state", default=None, help="Optional OAuth state value")
    aidex_auth.add_argument(
        "--code",
        default=None,
        help="Authorization code or full redirect URL (skips the interactive prompt)",
    )

    aidex_sync = sub.add_parser(
        "aidex-sync",
        help="Sync official MicroTech LinX/AiDEX glucose data into the shared store",
    )
    aidex_sync.add_argument("--user-id", required=True)
    aidex_sync.add_argument("--days", type=int, default=1, help="Backfill window in days")
    aidex_sync.add_argument("--force", action="store_true")
    aidex_sync.add_argument(
        "--incremental",
        action="store_true",
        help="Continue from the latest stored AiDEX point with an overlap window",
    )
    aidex_sync.add_argument("--overlap-minutes", type=int, default=15)
    aidex_sync.add_argument("--bootstrap-hours", type=int, default=24)

    aidex_status = sub.add_parser(
        "aidex-status",
        help="Check AiDEX credentials, authorization, storage, cron script, and optional live API access",
    )
    aidex_status.add_argument("--user-id", required=True)
    aidex_status.add_argument(
        "--live",
        action="store_true",
        help="Use the stored token to verify the official data-range endpoint",
    )

    source_poll = sub.add_parser(
        "source-poll",
        help=(
            "Poll an xDrip/Juggluco/Nightscout-compatible HTTP feed once, "
            "archive raw payload, insert deduped points, and persist detected events."
        ),
    )
    source_poll.add_argument("--user-id", required=True)
    source_poll.add_argument("--kind", required=True, choices=["xdrip", "juggluco", "nightscout"])
    source_poll.add_argument("--url", required=True)
    source_poll.add_argument("--count", type=int, default=12)
    source_poll.add_argument("--source", default=None, help="Optional stable source label override")
    source_poll.add_argument("--db-path", default=None, help="SQLite DB path (default: runtime DB)")
    source_poll.add_argument("--expected-interval-min", type=int, default=5)

    sub.add_parser(
        "bridge-status",
        help="Verify the configured Android/Juggluco/xDrip/Nightscout bridge without writing data",
    )
    sub.add_parser(
        "bridge-poll",
        help="Poll the configured Android bridge once into the canonical Hermes CGM store",
    )

    accept = sub.add_parser(
        "hermes-accept",
        help="Run an isolated Hermes CGM L0-L3, RAG, prompt, and restart acceptance",
    )
    accept.add_argument("--source-db", default=None, help="Canonical DB snapshot source")
    accept.add_argument("--duration-hours", type=int, choices=[24, 48, 72], default=72)
    accept.add_argument("--user-id", default="demo-prediabetes-14d-v2")
    accept.add_argument("--output-dir", default=None)
    accept.add_argument("--timezone", default=default_timezone())
    accept.add_argument("--hermes-home", default=None)
    accept.add_argument("--hermes-bin", default=None)
    accept.add_argument("--provider", default=None)
    accept.add_argument(
        "--provider-user-agent",
        default=None,
        help="Optional User-Agent applied only to the isolated provider smoke/model calls",
    )
    accept.add_argument(
        "--provider-max-tokens",
        type=int,
        default=None,
        help="Optional legacy max_tokens body field for custom provider compatibility",
    )
    accept.add_argument("--model", default="gpt-5.5")
    accept.add_argument("--deliver", default=None)
    accept.add_argument("--max-model-calls", type=int, default=30)
    accept.add_argument("--max-external-messages", type=int, default=6)
    accept.add_argument("--activate-on-pass", action="store_true")
    accept.add_argument("--no-model", action="store_true", help="Only run deterministic acceptance")
    accept.add_argument("--send-external", action="store_true", help="Allow capped real-channel delivery")
    accept.add_argument("--timeout-seconds", type=int, default=180)

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
    seed_demo.add_argument("--user-id", default=default_user_id())
    seed_demo.add_argument("--timezone", default=default_timezone())
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
    push_tick.add_argument("--user-id", default=default_user_id())
    push_tick.add_argument("--now", default=None, help="ISO 8601 datetime override (testing)")
    push_tick.add_argument("--timezone", default=default_timezone())
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

    kb_pending = sub.add_parser(
        "kb-pending",
        help="List unverified authoritative KB cards for clinician review",
    )
    kb_pending.add_argument("--kb", default=None, help="Knowledge-base JSON path")
    kb_pending.add_argument("--format", choices=["table", "json"], default="table")
    kb_pending.add_argument("--limit", type=int, default=None)

    kb_approve = sub.add_parser(
        "kb-approve",
        help="Clinician sign-off for one authoritative KB card",
    )
    kb_approve.add_argument("--card-id", required=True)
    kb_approve.add_argument("--reviewer", required=True)
    kb_approve.add_argument("--reviewed-at", default=None)
    kb_approve.add_argument("--kb", default=None, help="Knowledge-base JSON path")

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
    hermes_install.add_argument("--dry-run", action="store_true")
    hermes_install.add_argument(
        "--seed-demo",
        action="store_true",
        help="After install, seed demo CGM data into the canonical store (first-run convenience)",
    )

    migrate_db = sub.add_parser(
        "migrate-db",
        help="Migrate the legacy .runtime store (DB + key) to the canonical Hermes path",
    )
    migrate_db.add_argument("--dry-run", action="store_true")
    migrate_db.add_argument(
        "--force", action="store_true", help="overwrite an existing target (backed up first)"
    )

    return parser
