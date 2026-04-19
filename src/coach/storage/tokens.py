from pathlib import Path
from .db import connect


def upsert(db_path: Path, athlete_id: str, *, access: str, refresh: str,
           expires_at: int) -> None:
    with connect(db_path) as c:
        c.execute("""
            INSERT INTO strava_tokens (athlete_id, access_token, refresh_token, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(athlete_id) DO UPDATE SET
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,
                expires_at=excluded.expires_at
        """, (athlete_id, access, refresh, expires_at))


def get(db_path: Path, athlete_id: str) -> dict | None:
    with connect(db_path) as c:
        r = c.execute("SELECT * FROM strava_tokens WHERE athlete_id=?",
                      (athlete_id,)).fetchone()
        return dict(r) if r else None
