import json
import time
from pathlib import Path
import httpx
import pytest
import respx
from coach.storage.db import apply_migrations
from coach.storage import tokens
from coach.strava.client import StravaClient


FIXTURE = Path(__file__).parent / "fixtures" / "strava_activity.json"


def _setup(tmp_path):
    db = tmp_path / "t.db"
    apply_migrations(db)
    tokens.upsert(db, "a1", access="expired", refresh="R0",
                  expires_at=int(time.time()) - 10)
    return db


@respx.mock
async def test_get_activity_refreshes_when_expired(tmp_path):
    db = _setup(tmp_path)
    respx.post("https://www.strava.com/oauth/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "A1", "refresh_token": "R1",
            "expires_at": int(time.time()) + 3600}))
    respx.get("https://www.strava.com/api/v3/activities/12345").mock(
        return_value=httpx.Response(200, json=json.loads(FIXTURE.read_text())))

    client = StravaClient(db, "a1", client_id="cid", client_secret="csec",
                          initial_refresh_token="R0")
    activity = await client.get_activity(12345)

    assert activity["distance_km"] == 8.3
    assert activity["duration_min"] == 45
    assert activity["avg_hr"] == 152
    assert activity["start_date"] == "2026-04-18T06:30:00"
    # persisted rotated tokens
    t = tokens.get(db, "a1")
    assert t["access_token"] == "A1"
    assert t["refresh_token"] == "R1"


@respx.mock
async def test_list_recent_since(tmp_path):
    db = _setup(tmp_path)
    tokens.upsert(db, "a1", access="A", refresh="R",
                  expires_at=int(time.time()) + 3600)
    respx.get("https://www.strava.com/api/v3/athlete/activities").mock(
        return_value=httpx.Response(200, json=[
            {"id": 1, "name": "A", "type": "Run", "distance": 1000.0,
             "moving_time": 600, "average_heartrate": None,
             "start_date_local": "2026-04-18T10:00:00",
             "athlete": {"id": 9999}}]))
    client = StravaClient(db, "a1", client_id="cid", client_secret="csec",
                          initial_refresh_token="R")
    out = await client.list_recent_since("2026-04-17T00:00:00Z")
    assert len(out) == 1
    assert out[0]["distance_km"] == 1.0
    assert out[0]["avg_hr"] is None
