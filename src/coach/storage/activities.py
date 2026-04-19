import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .db import connect


def upsert(db_path: Path, activity: dict, raw: dict) -> None:
    with connect(db_path) as c:
        c.execute("""
            INSERT INTO activities (id, athlete_id, start_date, name, type,
                distance_km, duration_min, avg_hr, raw_json)
            VALUES (:id, :athlete_id, :start_date, :name, :type,
                :distance_km, :duration_min, :avg_hr, :raw_json)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, type=excluded.type,
                distance_km=excluded.distance_km,
                duration_min=excluded.duration_min,
                avg_hr=excluded.avg_hr, raw_json=excluded.raw_json
        """, {**activity, "raw_json": json.dumps(raw)})


def recent(db_path: Path, athlete_id: str, weeks: int = 3) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=weeks)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    with connect(db_path) as c:
        rows = c.execute("""
            SELECT * FROM activities
            WHERE athlete_id = ? AND start_date >= ?
            ORDER BY start_date DESC
        """, (athlete_id, cutoff)).fetchall()
        return [dict(r) for r in rows]


def get(db_path: Path, activity_id: int) -> dict | None:
    with connect(db_path) as c:
        r = c.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
        return dict(r) if r else None


def most_recent_start_date(db_path: Path, athlete_id: str) -> str | None:
    with connect(db_path) as c:
        r = c.execute(
            "SELECT start_date FROM activities WHERE athlete_id=? "
            "ORDER BY start_date DESC LIMIT 1",
            (athlete_id,)).fetchone()
        return r[0] if r else None
