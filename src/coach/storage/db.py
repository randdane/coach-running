# src/coach/storage/db.py
from contextlib import contextmanager
from pathlib import Path
import sqlite3

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@contextmanager
def connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def apply_migrations(db_path: Path) -> None:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with connect(db_path) as c:
        current = c.execute("PRAGMA user_version").fetchone()[0]
        for path in files:
            version = int(path.name.split("_")[0])
            if version <= current:
                continue
            with c:  # implicit transaction
                c.executescript(path.read_text())
                c.execute(f"PRAGMA user_version = {version}")
