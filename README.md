# Hermes CGM Agent

Personal `CGM` AI agent capability layer built around `Hermes Agent`.

Current implementation priority:

1. Treat `Hermes CLI` as the main shell.
2. Keep this repository as the `CGM capability layer`.
3. Persist CGM data, memory, reports, and audit locally.
4. Build `CGM` modules behind tool and storage boundaries.
5. Keep open-ended chat delegated to `Hermes`.

## Quick Commands

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent status
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent dev-status
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent tools
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent hermes-version
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent hermes-install --dry-run
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent context-build --user-id user-1 --anchor-at 2026-06-15T00:00:00+00:00
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent memory-synthesize --user-id user-1 --window-start 2026-05-31T00:00:00+00:00 --window-end 2026-06-01T00:00:00+00:00 --period daily
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent kb-validate
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent eval-rag
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent seed-demo --db-path ./.runtime/demo.db
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent push-tick --user-id user-1
```

`push-tick` is the cron-callable tiered-push scheduler. The project owns the
**policy** (which of daily/weekly/monthly digests is due), the **content** (warm
summaries) and the **state** (idempotent `push_events` + a silence window); the
**timing and delivery channel** are driven externally by Hermes/cron. Each tick
also applies *silent-consent*: a low-commitment `candidate` behavioural
hypothesis left unobjected for the silence window advances to `observing` — never
`stable` (that needs evidence), never archived hypotheses, never safety/medical
content, and always audited and reversible via `memory.correct`.

`seed-demo` runs the full data→memory→recall loop on a CGM CSV (default
`examples/cgm_test_dataset/cgm_3x14.csv`): it imports points, derives L1 episodes
from detected glucose events (real per-day facts), consolidates them into L2
beliefs and L3 hypotheses across distinct days, synthesizes a warm summary, and
prints a memory-recall sample plus the USER.md L2 projection. Point it at a
throwaway `--db-path` to inspect a populated database without touching the
runtime DB.

Run tests:

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests
```

Memory retrieval runtime notes:

- Default authoritative medical RAG is sparse-only BM25 over curated claim cards
  plus tags/synonyms/population metadata. It does not load embedding models by
  default.
- Personal memory uses a split path: L2/L3 profile and hypotheses are injected
  directly from SQLite; L1 episodes use BM25 at small scale and can switch to
  semantic hybrid retrieval when the episode count crosses the configured
  threshold.
- `CGM_AGENT_USE_HASHING_EMBEDDER=1` forces the deterministic hashing embedder
  for offline/dev tests only.
- `CGM_AGENT_ENABLE_SEMANTIC_RETRIEVAL=1` intentionally enables the real
  sentence-transformer dense path.
- `CGM_AGENT_PERSONAL_SEMANTIC_MIN_EPISODES=200` controls the personal L1
  automatic semantic threshold.
- Optional custom model override:
  `CGM_AGENT_EMBED_MODEL=paraphrase-multilingual-MiniLM-L12-v2`
- Install optional semantic dependencies with:
  `pip install -e ".[semantic]"`

## Structure

- `src/hermes_cgm_agent/domain/` - executable `CGM` domain contracts.
- `src/hermes_cgm_agent/hermes_plugins/` - installer for Hermes-side plugin activation.
- `src/hermes_cgm_agent/services/` - CGM analytics, data, memory, reports, RAG, tools, and audit services.
- `src/hermes_cgm_agent/services/analytics/` - reproducible `CGM` metric calculations.
- `src/hermes_cgm_agent/services/data/` - `CGM` repository service.
- `tests/fixtures/` - sample CGM CSV/JSON import files.
- `src/hermes_cgm_agent/services/tools/` - Hermes-facing `CGM` tool registry and executor.
- `src/hermes_cgm_agent/storage/` - `SQLite`-backed persistence.
- `integrations/hermes/cgm/` - Hermes in-process `cgm` tool plugin.
- `integrations/hermes/cgm_memory/` - Hermes external memory provider wrapper.
- `schemas/` - schema notes and future JSON Schema exports.
- `prompts/` - project prompt assets.
- `eval/` - evaluation samples and runners.

`Hermes` is expected to be installed on this machine. The adapter auto-discovers `hermes` from `PATH`, with per-platform fallbacks such as:

- macOS / Linux: `~/.hermes/bin/hermes`, `~/.local/bin/hermes`
- Windows: `%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe`

To install or refresh the Hermes-side user plugins and activate the provider/toolset:

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent hermes-install
```

Use `--dry-run` first to inspect target plugin paths and Hermes commands without
writing into `~/.hermes`.

This command:

- installs `cgm` and `cgm_memory` into `~/.hermes/plugins/`
- writes a project-root marker under `~/.hermes/`
- enables the `cgm` plugin in Hermes
- activates `cgm_memory` as the external memory provider
- installs this project into Hermes' own runtime venv when available

Knowledge-base operations:

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent kb-ingest --pdf src/hermes_cgm_agent/knowledge/pdfs/battelino-2019-tir.pdf --out-dir src/hermes_cgm_agent/knowledge/review_queue --kb-version kb-2026-06-candidate
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent kb-ingest-llm --pdf src/hermes_cgm_agent/knowledge/pdfs/battelino-2019-tir.pdf --out-dir src/hermes_cgm_agent/knowledge/review_queue --kb-version kb-2026-06-auto-v1 --mode auto
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent kb-merge --candidates src/hermes_cgm_agent/knowledge/review_queue/battelino-2019-tir.candidates.json --dry-run
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent kb-validate
```

`kb-ingest` is the lightweight keyword fallback. The default production path is
`kb-ingest-llm`, which delegates claim-card extraction to Hermes CLI. Text-heavy
pages use paged text prompts; table, figure, or low-text pages are rendered to
PNG and passed with `hermes chat --image`.

Non-medical operator workflow:

1. Run `kb-ingest-llm --mode auto` for one PDF, or `kb-ingest-batch` for
   manifest-prioritized PDFs.
2. Preview with `kb-merge --dry-run`, then merge accepted candidates. Merge
   always forces `verified=false`.
3. Run `kb-validate` and `eval-rag`.

Production cards in `authoritative_kb.json` may remain `verified=false` as
machine-extracted guideline drafts. A card may only become `verified=true` after
external review provenance (`reviewer` or `reviewed_at`) is recorded.

Runtime data is stored under the project's `.runtime/` directory by default, for example:

`./.runtime/`

The SQLite file is created with `0600` permissions on Unix-like systems. Sensitive health payload columns are application-encrypted with a Fernet key stored at `.runtime/storage.key` by default. Override with `CGM_AGENT_STORAGE_KEY_PATH` or provide `CGM_AGENT_STORAGE_KEY` in managed deployments.
