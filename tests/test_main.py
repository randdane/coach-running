from fastapi.testclient import TestClient
import pytest


def _env(monkeypatch, tmp_path):
    for k, v in {
        "ATHLETE_ID": "9999", "STRAVA_CLIENT_ID": "c", "STRAVA_CLIENT_SECRET": "s",
        "STRAVA_REFRESH_TOKEN": "r", "WEBHOOK_SECRET": "s" * 32,
        "NTFY_BASE_URL": "http://ntfy", "NTFY_TOPIC": "coach",
        "LITELLM_MASTER_KEY": "k", "DATA_DIR": str(tmp_path),
        "COACH_MODEL": "gpt-4o",
    }.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "athlete_context.md").write_text("- seed\n")
    (tmp_path / "memory" / "training_plan.md").write_text("plan\n")


def test_healthz(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    from coach.config import get_settings
    get_settings.cache_clear()
    from coach.main import create_app
    app = create_app(start_scheduler=False)
    c = TestClient(app)
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True
