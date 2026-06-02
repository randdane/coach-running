import json
from datetime import datetime, timedelta, timezone
from coach.storage.db import apply_migrations, connect
from coach.storage import activities


def _setup(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    return db


def _recent_iso(days_ago: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def test_upsert_and_recent(tmp_path):
    db = _setup(tmp_path)
    a1 = {"id": 1, "athlete_id": "a", "start_date": _recent_iso(1),
          "name": "Easy", "type": "Run", "distance_km": 5.1,
          "duration_min": 30, "avg_hr": 140}
    activities.upsert(db, a1, raw={"foo": "bar"})
    activities.upsert(db, a1, raw={"foo": "bar"})  # idempotent
    rows = activities.recent(db, "a", weeks=4)
    assert len(rows) == 1
    assert rows[0]["name"] == "Easy"
    assert json.loads(rows[0]["raw_json"]) == {"foo": "bar"}


def test_most_recent_start_date(tmp_path):
    db = _setup(tmp_path)
    earlier = _recent_iso(3)
    later = _recent_iso(1)
    activities.upsert(db, {"id": 1, "athlete_id": "a",
        "start_date": earlier, "name": "x", "type": "Run",
        "distance_km": 1, "duration_min": 1, "avg_hr": None}, raw={})
    activities.upsert(db, {"id": 2, "athlete_id": "a",
        "start_date": later, "name": "y", "type": "Run",
        "distance_km": 1, "duration_min": 1, "avg_hr": None}, raw={})
    assert activities.most_recent_start_date(db, "a") == later


def test_most_recent_start_date_none_when_empty(tmp_path):
    db = _setup(tmp_path)
    assert activities.most_recent_start_date(db, "a") is None
