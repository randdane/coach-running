import json
from coach.storage.db import apply_migrations, connect
from coach.storage import activities


def _setup(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    return db


def test_upsert_and_recent(tmp_path):
    db = _setup(tmp_path)
    a1 = {"id": 1, "athlete_id": "a", "start_date": "2026-04-10T10:00:00Z",
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
    activities.upsert(db, {"id": 1, "athlete_id": "a",
        "start_date": "2026-04-10T10:00:00Z", "name": "x", "type": "Run",
        "distance_km": 1, "duration_min": 1, "avg_hr": None}, raw={})
    activities.upsert(db, {"id": 2, "athlete_id": "a",
        "start_date": "2026-04-12T10:00:00Z", "name": "y", "type": "Run",
        "distance_km": 1, "duration_min": 1, "avg_hr": None}, raw={})
    assert activities.most_recent_start_date(db, "a") == "2026-04-12T10:00:00Z"


def test_most_recent_start_date_none_when_empty(tmp_path):
    db = _setup(tmp_path)
    assert activities.most_recent_start_date(db, "a") is None
