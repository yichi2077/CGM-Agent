from __future__ import annotations

import json
import hashlib
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


DERIVED_TABLES = (
    "l1_episodes",
    "l2_profile_items",
    "l3_hypotheses",
    "memory_candidates",
    "memory_summaries",
    "reports",
    "push_events",
    "pending_interactions",
    "unread_badges",
    "safety_red_zone_events",
    "audit_logs",
)


def _copy_sqlite(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    src = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _protect(path: Path) -> None:
    if os.name != "nt":
        try:
            path.chmod(0o700)
        except OSError:
            pass
        return
    user = _current_identity()
    if not user:
        return
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
        check=False,
        capture_output=True,
        timeout=10,
    )


def _current_identity() -> str:
    """Return the account running the acceptance process, not its parent env."""

    try:
        result = subprocess.run(
            ["whoami"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        identity = result.stdout.strip()
        if identity:
            return identity
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("USERNAME", "")


def _copy_plugin(src_home: Path, dst_home: Path, name: str) -> None:
    src = src_home / "plugins" / name
    if not src.exists():
        return
    dst = dst_home / "plugins" / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


class SnapshotManager:
    def __init__(self, source_db: Path, root: Path, source_home: Path) -> None:
        self.source_db = source_db.resolve()
        self.root = root.resolve()
        self.source_home = source_home.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        _protect(self.root)

    @property
    def retrieval_db(self) -> Path:
        return self.root / "retrieval-copy" / "app.db"

    @property
    def rebuild_db(self) -> Path:
        return self.root / "rebuild-copy" / "app.db"

    @property
    def validation_home(self) -> Path:
        return self.root / "hermes-home"

    def prepare(
        self,
        *,
        provider_user_agent: str | None = None,
        provider_max_tokens: int | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        if not self.source_db.exists():
            raise FileNotFoundError(f"source database does not exist: {self.source_db}")
        source_key = self.source_db.parent / "storage.key"
        if not source_key.exists():
            raise FileNotFoundError(f"source storage key does not exist: {source_key}")
        _copy_sqlite(self.source_db, self.retrieval_db)
        _copy_sqlite(self.source_db, self.rebuild_db)
        for db in (self.retrieval_db, self.rebuild_db):
            shutil.copy2(source_key, db.parent / "storage.key")
        self._prepare_hermes_home()
        request_result = self.configure_provider_request(
            user_agent=provider_user_agent,
            max_tokens=provider_max_tokens,
            provider=provider,
        )
        return {
            "source_db": str(self.source_db),
            "retrieval_db": str(self.retrieval_db),
            "rebuild_db": str(self.rebuild_db),
            "validation_home": str(self.validation_home),
            "source_db_size": self.source_db.stat().st_size,
            "source_db_sha256": _sha256(self.source_db),
            "retrieval_db_sha256": _sha256(self.retrieval_db),
            "rebuild_db_sha256": _sha256(self.rebuild_db),
            "storage_key_sha256": _sha256(source_key),
            "provider_request": request_result,
        }

    def configure_provider_request(
        self,
        *,
        user_agent: str | None = None,
        max_tokens: int | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Apply an explicitly requested model header to the copied profile.

        Hermes reads ``model.default_headers`` when building both its primary
        and auxiliary OpenAI clients. Keeping this opt-in and confined to the
        validation home avoids changing the user's profile merely by running a
        deterministic or failed acceptance. Values containing control
        characters are rejected because they cannot be valid HTTP headers.
        """

        value = (user_agent or "").strip()
        if value and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("provider_user_agent contains an HTTP control character")
        if len(value) > 256:
            raise ValueError("provider_user_agent is too long")
        if max_tokens is not None and (isinstance(max_tokens, bool) or max_tokens <= 0):
            raise ValueError("provider_max_tokens must be a positive integer")
        if not value and max_tokens is None:
            return {"configured": False}
        config_path = self.validation_home / "config.yaml"
        if not config_path.exists():
            raise FileNotFoundError("validation Hermes config.yaml is missing")
        try:
            import yaml

            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if not isinstance(config, dict):
                raise ValueError("Hermes config.yaml must be a mapping")
            model = config.setdefault("model", {})
            if not isinstance(model, dict):
                raise ValueError("Hermes model config must be a mapping")
            headers = model.setdefault("default_headers", {})
            if value:
                if not isinstance(headers, dict):
                    raise ValueError("Hermes model.default_headers must be a mapping")
                headers["User-Agent"] = value
            if max_tokens is not None:
                providers = config.get("custom_providers")
                if not isinstance(providers, list):
                    raise ValueError("Hermes custom_providers must be a list")
                provider_key = (provider or "").strip().lower()
                if provider_key.startswith("custom:"):
                    provider_key = provider_key.split(":", 1)[1]
                updated = 0
                for entry in providers:
                    if not isinstance(entry, dict):
                        continue
                    if provider_key:
                        identities = {
                            str(entry.get("provider_key") or "").strip().lower(),
                            str(entry.get("name") or "").strip().lower(),
                        }
                        if provider_key not in identities:
                            continue
                    extra_body = entry.setdefault("extra_body", {})
                    if not isinstance(extra_body, dict):
                        raise ValueError("custom provider extra_body must be a mapping")
                    extra_body["max_tokens"] = max_tokens
                    updated += 1
                if not updated:
                    raise ValueError("no custom provider entry found for max_tokens override")
            config_path.write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        except (OSError, ValueError):
            raise
        except Exception as exc:  # noqa: BLE001 - surface malformed validation config
            raise ValueError(f"could not update validation Hermes headers: {type(exc).__name__}") from exc
        result: dict[str, Any] = {"configured": True}
        if value:
            result["user_agent"] = value
        if max_tokens is not None:
            result["max_tokens"] = max_tokens
        return result

    def configure_provider_headers(self, user_agent: str | None) -> dict[str, Any]:
        """Backward-compatible wrapper for callers that only need a header."""

        return self.configure_provider_request(user_agent=user_agent)

    def clear_derived(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with sqlite3.connect(str(self.rebuild_db)) as conn:
            for table in DERIVED_TABLES:
                try:
                    counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    conn.execute(f"DELETE FROM {table}")
                except sqlite3.OperationalError:
                    counts[table] = 0
            conn.commit()
        return counts

    def _prepare_hermes_home(self) -> None:
        self.validation_home.mkdir(parents=True, exist_ok=True)
        for name in ("config.yaml", ".env", "SOUL.md"):
            src = self.source_home / name
            if src.exists():
                shutil.copy2(src, self.validation_home / name)
        for name in ("cgm", "cgm_memory"):
            _copy_plugin(self.source_home, self.validation_home, name)
        marker = self.validation_home / "cgm-agent-project-root.txt"
        marker.write_text(str(Path(__file__).resolve().parents[4]), encoding="utf-8")
        _protect(self.validation_home)

    def manifest(self) -> dict[str, Any]:
        return {
            "source_db": str(self.source_db),
            "source_db_size": self.source_db.stat().st_size,
            "retrieval_db": str(self.retrieval_db),
            "rebuild_db": str(self.rebuild_db),
            "validation_home": str(self.validation_home),
            "derived_tables": list(DERIVED_TABLES),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
