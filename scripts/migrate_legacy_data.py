"""Thin shim: migrate the legacy ``.runtime`` CGM store (DB + key) to the
canonical Hermes path.

The real logic lives in :mod:`hermes_cgm_agent.migrate` (importable + tested).
Prefer ``python -m hermes_cgm_agent migrate-db``; this shim exists for direct
``python scripts/migrate_legacy_data.py`` invocation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hermes_cgm_agent.migrate import OK_STATUSES, migrate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy .runtime CGM store to the canonical path")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="overwrite an existing target (backed up first)")
    args = parser.parse_args()
    result = migrate(dry_run=args.dry_run, force=args.force)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in OK_STATUSES else 1


if __name__ == "__main__":
    raise SystemExit(main())
