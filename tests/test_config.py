# tests/test_config.py
import pytest
from pydantic import ValidationError
from coach.config import Settings


def test_missing_required_fails(monkeypatch):
    for k in ("ATHLETE_ID", "STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET",
              "STRAVA_REFRESH_TOKEN", "WEBHOOK_SECRET", "NTFY_BASE_URL",
              "NTFY_TOPIC", "LITELLM_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_defaults(monkeypatch):
    for k, v in {
        "ATHLETE_ID": "a1", "STRAVA_CLIENT_ID": "x", "STRAVA_CLIENT_SECRET": "y",
        "STRAVA_REFRESH_TOKEN": "z", "WEBHOOK_SECRET": "s" * 32,
        "NTFY_BASE_URL": "http://ntfy", "NTFY_TOPIC": "coach",
        "LITELLM_MASTER_KEY": "k",
    }.items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.morning_cron == "0 6 * * *"
    assert s.poll_cron == "30 22 * * *"
    assert s.webhook_delay_seconds == 15 * 60
    assert s.webhook_rate_limit == "30/minute"
    assert s.coach_model == "gpt-4o"
    assert s.memory_size_warn_kb == 20
    assert s.litellm_base_url == "http://litellm:4000"
