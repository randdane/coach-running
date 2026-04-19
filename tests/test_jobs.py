from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from coach.storage.db import apply_migrations
from coach.storage import activities, messages
from coach import jobs, memory


class StubLLM:
    def __init__(self, response="coach says hi", tool_text: str | None = None):
        self.response = response
        self.tool_text = tool_text
        self.last_kwargs = None

    def chat(self, *, model, system_prompt, user_prompt, on_observation,
             max_tool_calls):
        self.last_kwargs = {"model": model, "system_prompt": system_prompt,
                            "user_prompt": user_prompt}
        calls = []
        if self.tool_text:
            on_observation(self.tool_text)
            calls.append({"text": self.tool_text})
        return self.response, calls


class StubNotify:
    def __init__(self):
        self.calls = []

    async def send(self, *, title, body):
        self.calls.append((title, body))


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    apply_migrations(db)
    memdir = tmp_path / "memory"
    memdir.mkdir()
    (memdir / "athlete_context.md").write_text("- seed\n")
    (memdir / "training_plan.md").write_text("plan\n")
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "coach_voice.md").write_text("voice\n")
    return SimpleNamespace(db=db, memdir=memdir, prompts_dir=prompts_dir)


async def test_morning_checkin_saves_message_and_notifies(env):
    llm = StubLLM(response="morning!")
    notify = StubNotify()
    await jobs.morning_checkin(
        db_path=env.db, memory_dir=env.memdir, prompts_dir=env.prompts_dir,
        athlete_id="a1", model="m", trigger="scheduled",
        llm_chat=llm.chat, notify_send=notify.send,
        now=lambda: datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc))
    row = messages.latest(env.db)
    assert row["kind"] == "morning"
    assert row["trigger"] == "scheduled"
    assert row["response"] == "morning!"
    assert notify.calls[0][1] == "morning!"


async def test_post_run_review_deduplicates(env):
    llm = StubLLM(response="good run")
    notify = StubNotify()
    activity = {"id": 42, "athlete_id": "a1",
                "start_date": "2026-04-18T10:00:00Z",
                "name": "Tempo", "type": "Run", "distance_km": 8.0,
                "duration_min": 45, "avg_hr": 160}
    activities.upsert(env.db, activity, raw={})

    await jobs.post_run_review(
        db_path=env.db, memory_dir=env.memdir, prompts_dir=env.prompts_dir,
        athlete_id="a1", model="m", trigger="webhook",
        activity_id=42,
        llm_chat=llm.chat, notify_send=notify.send)
    await jobs.post_run_review(
        db_path=env.db, memory_dir=env.memdir, prompts_dir=env.prompts_dir,
        athlete_id="a1", model="m", trigger="poll",
        activity_id=42,
        llm_chat=llm.chat, notify_send=notify.send)
    rows = messages.list_recent(env.db)
    assert len(rows) == 1


async def test_post_run_review_appends_observation(env):
    llm = StubLLM(response="ok", tool_text="ankle felt good")
    notify = StubNotify()
    activity = {"id": 7, "athlete_id": "a1",
                "start_date": "2026-04-18T10:00:00Z",
                "name": "Easy", "type": "Run", "distance_km": 5.0,
                "duration_min": 30, "avg_hr": 140}
    activities.upsert(env.db, activity, raw={})
    await jobs.post_run_review(
        db_path=env.db, memory_dir=env.memdir, prompts_dir=env.prompts_dir,
        athlete_id="a1", model="m", trigger="webhook",
        activity_id=7,
        llm_chat=llm.chat, notify_send=notify.send)
    ctx = memory.read_athlete_context(env.memdir)
    assert "ankle felt good" in ctx
