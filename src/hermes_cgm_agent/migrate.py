"""Legacy CGM store migration (F1 / D045).

Moves a legacy standalone ``.runtime`` store to the canonical Hermes-home path
resolved by :func:`config.resolve_database_path`. The Fernet key is moved together
with the database — a database without its key is undecryptable. The operation is
non-destructive: it refuses to overwrite an existing target without ``force`` (and
backs the target up first when forced), and refuses entirely when the legacy key is
missing. No secret bytes are ever printed; only paths and a status are returned.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from hermes_cgm_agent.config import DEFAULT_DB_PATH, resolve_database_path

OK_STATUSES = {"nothing", "planned", "migrated"}


def _canonical_target() -> Path:
    return resolve_database_path(os.getenv("HERMES_HOME") or None)


def migrate(
    *,
    dry_run: bool = False,
    force: bool = False,
    legacy_db: Path | str | None = None,
    legacy_key: Path | str | None = None,
    target_db: Path | str | None = None,
) -> dict:
    """Migrate the legacy store to the canonical path. Returns a status dict."""
    legacy_db = Path(legacy_db) if legacy_db else DEFAULT_DB_PATH
    legacy_key = Path(legacy_key) if legacy_key else legacy_db.parent / "storage.key"
    target_db = Path(target_db) if target_db else _canonical_target()
    target_key = target_db.parent / "storage.key"

    if legacy_db.resolve() == target_db.resolve():
        return {"status": "nothing", "message": "legacy and target are the same store; nothing to migrate."}
    if not legacy_db.exists():
        return {"status": "nothing", "message": "no legacy .runtime store found; nothing to migrate."}
    if not legacy_key.exists():
        return {
            "status": "refused_missing_key",
            "message": (
                "legacy storage.key not found beside the legacy database; migrating without it "
                "would leave the data undecryptable. Aborting (no changes made)."
            ),
        }
    if target_db.exists() and not force:
        return {
            "status": "refused_exists",
            "message": (
                f"target store already exists at {target_db}; re-run with --force to overwrite "
                "(the existing target will be backed up first)."
            ),
        }
    if dry_run:
        return {
            "status": "planned",
            "message": "dry-run: no changes made.",
            "copies": [f"{legacy_db} -> {target_db}", f"{legacy_key} -> {target_key}"],
        }

    target_db.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if target_db.exists() and force:
        backup = target_db.with_suffix(target_db.suffix + ".bak")
        shutil.copy2(target_db, backup)
        if target_key.exists():
            shutil.copy2(target_key, target_key.with_suffix(target_key.suffix + ".bak"))

    shutil.copy2(legacy_db, target_db)
    shutil.copy2(legacy_key, target_key)
    for path in (target_db, target_key):
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    result = {"status": "migrated", "message": f"migrated database + key to {target_db.parent}"}
    if backup is not None:
        result["backup"] = str(backup)
    return result
