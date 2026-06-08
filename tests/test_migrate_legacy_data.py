from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.migrate import migrate


class MigrateLegacyDataTests(unittest.TestCase):
    """F1 / C3: legacy store migration moves DB + key together, non-destructively."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.legacy_db = base / "runtime" / "app.db"
        self.legacy_key = base / "runtime" / "storage.key"
        self.target_db = base / "hermes" / "cgm-agent" / "app.db"
        self.legacy_db.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _write(path: Path, content: bytes = b"x") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _migrate(self, **kw):
        return migrate(
            legacy_db=self.legacy_db,
            legacy_key=self.legacy_key,
            target_db=self.target_db,
            **kw,
        )

    def test_nothing_when_no_legacy(self) -> None:
        self.assertEqual(self._migrate()["status"], "nothing")

    def test_refuse_when_key_missing(self) -> None:
        self._write(self.legacy_db)
        result = self._migrate()
        self.assertEqual(result["status"], "refused_missing_key")
        self.assertFalse(self.target_db.exists())

    def test_dry_run_makes_no_changes(self) -> None:
        self._write(self.legacy_db)
        self._write(self.legacy_key)
        result = self._migrate(dry_run=True)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(self.target_db.exists())

    def test_migrates_db_and_key_together(self) -> None:
        self._write(self.legacy_db, b"DBDATA")
        self._write(self.legacy_key, b"KEYDATA")
        result = self._migrate()
        self.assertEqual(result["status"], "migrated")
        self.assertEqual(self.target_db.read_bytes(), b"DBDATA")
        self.assertEqual((self.target_db.parent / "storage.key").read_bytes(), b"KEYDATA")

    def test_refuse_existing_target_without_force(self) -> None:
        self._write(self.legacy_db)
        self._write(self.legacy_key)
        self._write(self.target_db, b"OLD")
        result = self._migrate()
        self.assertEqual(result["status"], "refused_exists")
        self.assertEqual(self.target_db.read_bytes(), b"OLD")

    def test_force_backs_up_then_overwrites(self) -> None:
        self._write(self.legacy_db, b"NEW")
        self._write(self.legacy_key, b"K")
        self._write(self.target_db, b"OLD")
        result = self._migrate(force=True)
        self.assertEqual(result["status"], "migrated")
        self.assertEqual(self.target_db.read_bytes(), b"NEW")
        self.assertTrue(Path(result["backup"]).exists())
        self.assertEqual(Path(result["backup"]).read_bytes(), b"OLD")

    def test_no_secret_bytes_in_result(self) -> None:
        self._write(self.legacy_db, b"DBDATA")
        self._write(self.legacy_key, b"SUPERSECRETKEY")
        result = self._migrate()
        self.assertNotIn("SUPERSECRETKEY", repr(result))


if __name__ == "__main__":
    unittest.main()
