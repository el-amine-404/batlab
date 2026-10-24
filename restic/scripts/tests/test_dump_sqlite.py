import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "dump-sqlite.py"
class DumpSqliteIntegrationTests(unittest.TestCase):
    def test_dump_uses_staged_helper_and_produces_checked_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            volumes = root / "volumes"
            source = volumes / "app" / "app.db"
            output = root / "dumps"
            source.parent.mkdir(parents=True)
            with sqlite3.connect(source) as database:
                database.execute("CREATE TABLE example (value TEXT)")
                database.execute("INSERT INTO example VALUES ('kept')")

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--volumes", str(volumes), "--out", str(output)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            copied = output / "app" / "app.db"
            with sqlite3.connect(copied) as database:
                self.assertEqual(database.execute("SELECT value FROM example").fetchone(), ("kept",))
            self.assertFalse((volumes / ".sqlite-staging").exists())


if __name__ == "__main__":
    unittest.main()
