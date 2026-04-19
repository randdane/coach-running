from coach.storage.db import apply_migrations
from coach.storage import tokens


def test_upsert_and_get(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    tokens.upsert(db, "a1", access="A", refresh="R", expires_at=123)
    t = tokens.get(db, "a1")
    assert t == {"athlete_id": "a1", "access_token": "A",
                 "refresh_token": "R", "expires_at": 123}
    tokens.upsert(db, "a1", access="A2", refresh="R2", expires_at=456)
    assert tokens.get(db, "a1")["access_token"] == "A2"


def test_get_missing(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    assert tokens.get(db, "nope") is None
