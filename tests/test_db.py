# tests/test_db.py
from pathlib import Path
from coach.storage.db import connect, apply_migrations


def test_migrations_bring_user_version_to_latest(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    with connect(db) as c:
        v = c.execute("PRAGMA user_version").fetchone()[0]
    assert v == 1


def test_migrations_are_idempotent(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    apply_migrations(db)
    with connect(db) as c:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"activities", "messages", "strava_tokens"}.issubset(tables)
