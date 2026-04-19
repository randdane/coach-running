import time
from datetime import datetime
from pathlib import Path
import httpx
from coach.storage import tokens

BASE = "https://www.strava.com"


def _map_activity(raw: dict) -> dict:
    return {
        "id": raw["id"],
        "name": raw["name"],
        "type": raw["type"],
        "distance_km": round(raw["distance"] / 1000, 2),
        "duration_min": round(raw["moving_time"] / 60),
        "avg_hr": int(raw["average_heartrate"]) if raw.get("average_heartrate") else None,
        "start_date": raw["start_date_local"],
        "owner_id": raw.get("athlete", {}).get("id"),
    }


class StravaClient:
    def __init__(self, db_path: Path, athlete_id: str, *, client_id: str,
                 client_secret: str, initial_refresh_token: str):
        self.db_path = db_path
        self.athlete_id = athlete_id
        self.client_id = client_id
        self.client_secret = client_secret
        # Seed tokens row if missing
        if tokens.get(db_path, athlete_id) is None:
            tokens.upsert(db_path, athlete_id, access="",
                          refresh=initial_refresh_token, expires_at=0)

    async def _access_token(self) -> str:
        t = tokens.get(self.db_path, self.athlete_id)
        assert t is not None
        if t["expires_at"] > int(time.time()) + 60:
            return t["access_token"]
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{BASE}/oauth/token", data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": t["refresh_token"],
                "grant_type": "refresh_token",
            })
            r.raise_for_status()
            body = r.json()
        tokens.upsert(self.db_path, self.athlete_id,
                      access=body["access_token"],
                      refresh=body["refresh_token"],
                      expires_at=int(body["expires_at"]))
        return body["access_token"]

    async def get_activity(self, activity_id: int) -> dict:
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/api/v3/activities/{activity_id}",
                            headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
        return _map_activity(r.json())

    async def list_recent_since(self, iso_after: str) -> list[dict]:
        token = await self._access_token()
        after_ts = int(datetime.fromisoformat(iso_after.replace("Z", "+00:00")).timestamp())
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/api/v3/athlete/activities",
                            headers={"Authorization": f"Bearer {token}"},
                            params={"after": after_ts, "per_page": 30})
            r.raise_for_status()
        return [_map_activity(x) for x in r.json()]
