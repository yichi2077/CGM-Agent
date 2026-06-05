# CGM-Agent × Hermes Integration — Verified Issue → Fix Matrix

This document is the design + review artifact for closing the gaps raised in the two
Hermes-authored review reports (`CGM-Agent_Hermes_Embedding_Review.md` and
`CGM-Agent_技术栈横纵分析报告.md`).

Every claim in the reports was re-verified against the actual local Hermes source
(`~/.hermes/hermes-agent`, v0.15.1) and this project's code before acting. Some
report claims were **overstated or wrong**; those are marked and NOT "fixed" blindly.

## Verification corrections to the reports

| Report claim | Verdict | Evidence |
|---|---|---|
| 1.3.3 — toolset `cgm` "won't resolve / invisible in `hermes tools`" | **WRONG** | `toolsets.py:584,699` — `_get_plugin_toolset_names()` auto-surfaces any toolset name used by a registered tool. Plugin toolsets resolve and list without being in `TOOLSETS`. No alias needed. |
| 4.1 — `ctx.register_memory_provider()` exists on PluginContext | **WRONG** | `hermes_cli/plugins.py` has no such method. Memory providers are discovered by the **`plugins/memory/` directory scan** (`plugins/memory/__init__.py`), loaded via a `register(collector)` hook where `collector.register_memory_provider(provider)` is captured, or via a `MemoryProvider` subclass fallback. |
| 2.2.x — provider contract gaps | **CONFIRMED** | `agent/memory_provider.py` ABC vs `services/memory/provider.py`. |
| 14.2 — context engine is a single global slot | **CONFIRMED** | `plugins.py:498 register_context_engine` rejects a 2nd engine. Activating a CGM-only engine would break general compression — so we scaffold but DO NOT auto-activate. |

## Issue → Fix matrix

| # | Report ID | Issue | Verdict | Resolution | Status |
|---|---|---|---|---|---|
| 1 | 1.3.1 | Only 1/10 tools exposed to Hermes | CONFIRMED | `cgm` plugin now registers all 8 capability tools in-process; `cgm_memory` provider exposes the 2 memory-review tools → 10 total | DONE |
| 2 | 1.3.2 / 10.2.x | subprocess per call bypasses dispatch infra | CONFIRMED | Handlers call an **in-process** `ToolExecutor`; results flow back through `registry.dispatch` → get `_sanitize_tool_error` + result-size budget for free. Subprocess kept only as a fallback. | DONE |
| 3 | 1.3.3 | toolset `cgm` invisible | WRONG | No code change; verified auto-surfaced. Documented. | N/A |
| 4 | (new, found in audit) | `hypothesis.update` + `delivery.send` registered but **no executor branch** | CONFIRMED bug | Implemented both executors (`delivery.send` supports `local_file`; `email`/`webhook` return `queued`). | DONE |
| 5 | 2.2.1 | `initialize()` drops kwargs | CONFIRMED | Captures `hermes_home`, `platform`, `agent_context`, `agent_identity`, `user_id`, `user_id_alt`, `parent_session_id`; sets a write-guard for non-primary contexts. | DONE |
| 6 | 2.2.2 | `handle_tool_call()` unimplemented | CONFIRMED | Implemented; routes `cgm_memory_confirm` / `cgm_memory_correct` to `MemoryReviewService`. | DONE |
| 7 | 2.2.5 | `on_session_switch()` unimplemented | CONFIRMED | Implemented; updates `_session_id`, flushes per-turn buffer on `reset`, invalidates on `rewound`. | DONE |
| 8 | 2.2.6 | `sync_turn(messages=...)` ignored | CONFIRMED | Captures conversation-relevant turns into pending memory candidates and keeps per-session notes for later compression/session-end handling. | DONE |
| 9 | 3.3.2 / 11.2.1 | `on_pre_compress` / `on_memory_write` unimplemented | CONFIRMED | `on_pre_compress` returns active L1/L3 plus recent turn digests; `on_memory_write` mirrors built-in writes into pending candidates. | DONE |
| 10 | 2.3.x | provider not discoverable | CONFIRMED | `integrations/hermes/cgm_memory/` provider plugin + `plugin.yaml` + `register()`; installer copies into `$HERMES_HOME/plugins/`; config guidance printed. | DONE |
| 11 | 9.2.1 / 13.1.1 | hardcoded Windows `hermes.exe` path | CONFIRMED | `config.py` resolves per-platform (PATH → common install dirs); no meaningless WindowsPath. | DONE |
| 12 | 9.2.3 | `CGM_AGENT_PROJECT_ROOT` brittle | CONFIRMED | Plugin resolves project root via env → marker file written by installer → import probe. | DONE |
| 13 | 8.2.x | no skills | CONFIRMED | `cgm-analysis` + `cgm-safety` SKILL.md added and installed. | DONE |
| 14 | 13.3.1 | no integration test for the Hermes-facing layer | CONFIRMED | `tests/test_hermes_plugin_integration.py` + provider-contract tests. | DONE |
| 15 | 13.1.2 | docs are Windows-only | CONFIRMED | AGENTS.md / README / BASELINE updated for the macOS reality + new integration. | DONE |
| 16 | 3.3.x / 14.2 | L0 context not injected; context-engine slot | CONFIRMED (constrained) | `on_pre_compress` preserves CGM memory across Hermes compaction. Full context-engine takeover intentionally NOT auto-enabled (single global slot would harm non-CGM chats); scaffold + rationale documented. | DONE (scoped) |
| 17 | 6.x / 7.x | cron / gateway not wired | CONFIRMED (infra) | Now mechanically possible: registered tools + skills + `delivery.send` local_file. Example cron job documented. Not auto-started (needs a running daemon + user creds). | DOC |

## Second-round audit (NEW-1 … NEW-5)

A follow-up independent review (2026-06-05) found defects introduced *by* the
first-round integration work. Resolutions:

| # | Issue | Severity | Resolution | Status |
|---|---|---|---|---|
| NEW-1 | `cgm` tool plugin and `cgm_memory` provider computed **different** DB paths (tools → `.runtime/app.db`, memory → `<hermes_home>/cgm-agent/app.db`), splitting glucose/events/reports away from the memory layer | HIGH | Single source of truth `config.resolve_database_path(hermes_home)` (env `CGM_AGENT_DB_PATH` → `<hermes_home>/cgm-agent/app.db` → project default). Both plugins call it. The standalone tool plugin — which never receives `hermes_home` in handler kwargs — derives it via Hermes's own `get_hermes_home()` (honors the ContextVar profile override that `os.environ` misses). | DONE |
| NEW-2 | `cgm` plugin rebuilt `SQLiteStore` + ran `CREATE TABLE IF NOT EXISTS` on **every** tool call (latency + WAL contention) | MEDIUM | Module-level `_EXECUTOR_CACHE` keyed by resolved DB path; store/executor built once per path. | DONE |
| NEW-3 | Installer would fail opaquely from a non-editable `pip install` (source tree unreachable) | LOW | `_resolve_project_root()` validates `integrations/hermes` exists (env `CGM_AGENT_PROJECT_ROOT` → `__file__` probe) and raises an actionable error. | DONE |
| NEW-4 | README still showed a PowerShell install block | LOW | Converted to the macOS/Linux `bash` invocation; Windows kept only as a fallback path note. | DONE |
| NEW-5 | `cgm_memory` wrapper kept a **duplicate hardcoded** copy of the memory tool schemas (drift risk vs. the inner provider) | LOW | `provider.MEMORY_TOOL_SCHEMAS` is the single source; both the inner provider and the wrapper's pre-`initialize()` fallback return a deepcopy of it. | DONE |

Regression coverage: `tests/test_config.py` (resolver precedence) and
`tests/test_hermes_plugin_integration.py` (both plugins share one resolved path;
executor is cached; schemas come from one source).

## Architecture decision (Caesar)

- **Wrap, don't rewrite.** CGM stays the capability layer; Hermes stays the shell.
- **In-process first.** The plugin adds the project `src` to
  `sys.path` and calls `ToolExecutor` directly; the legacy subprocess spike was removed.
- **Tool ownership split:** capability tools (data/report/rag/events/hypothesis/delivery)
  live in the `cgm` toolset via the standalone plugin; the two memory-review tools live
  with the memory provider so they route through `MemoryManager.handle_tool_call`.
- **Names:** Hermes/OpenAI function names can't contain `.`; CGM dotted names are mapped
  to `cgm_<group>_<action>` and translated back at dispatch.
