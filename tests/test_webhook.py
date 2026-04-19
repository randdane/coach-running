import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from coach.strava.webhook import build_router


class FakeScheduler:
    def __init__(self):
        self.scheduled = []

    def schedule_post_run(self, trigger, activity_id):
        self.scheduled.append((trigger, activity_id))
        return True


@pytest.fixture
def client():
    sched = FakeScheduler()
    app = FastAPI()
    app.include_router(build_router(
        secret="s" * 32, athlete_id="9999",
        on_create=sched.schedule_post_run))
    return TestClient(app), sched


def test_get_handshake(client):
    c, _ = client
    r = c.get(f"/webhook/strava/{'s'*32}",
              params={"hub.challenge": "CHAL",
                      "hub.verify_token": "x",
                      "hub.mode": "subscribe"})
    assert r.status_code == 200
    assert r.json() == {"hub.challenge": "CHAL"}


def test_post_create_triggers_schedule(client):
    c, sched = client
    payload = json.loads((Path(__file__).parent / "fixtures" /
                          "strava_webhook_create.json").read_text())
    r = c.post(f"/webhook/strava/{'s'*32}", json=payload)
    assert r.status_code == 200
    assert sched.scheduled == [("webhook", 12345)]


def test_post_wrong_secret_is_404(client):
    c, sched = client
    r = c.post("/webhook/strava/wrong", json={})
    assert r.status_code == 404
    assert sched.scheduled == []


def test_post_ignores_other_owner(client):
    c, sched = client
    r = c.post(f"/webhook/strava/{'s'*32}", json={
        "object_type": "activity", "aspect_type": "create",
        "object_id": 1, "owner_id": 1})
    assert r.status_code == 200
    assert sched.scheduled == []


def test_post_ignores_non_create(client):
    c, sched = client
    r = c.post(f"/webhook/strava/{'s'*32}", json={
        "object_type": "activity", "aspect_type": "update",
        "object_id": 1, "owner_id": 9999})
    assert r.status_code == 200
    assert sched.scheduled == []
