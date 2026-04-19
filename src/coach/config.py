# src/coach/config.py
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Identity & storage
    athlete_id: str
    data_dir: Path = Path("/data")
    tz: str = "America/New_York"

    # Strava
    strava_client_id: str
    strava_client_secret: str
    strava_refresh_token: str

    # Webhook
    webhook_secret: str = Field(min_length=16)
    webhook_delay_seconds: int = 15 * 60
    webhook_rate_limit: str = "30/minute"

    # Schedules
    morning_cron: str = "0 6 * * *"
    poll_cron: str = "30 22 * * *"

    # LLM
    coach_model: str = "gpt-4o"
    litellm_base_url: str = "http://litellm:4000"
    litellm_master_key: str
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Notify
    ntfy_base_url: str
    ntfy_topic: str

    # Memory
    memory_size_warn_kb: int = 20

    @property
    def db_path(self) -> Path:
        return self.data_dir / "coach.db"

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
