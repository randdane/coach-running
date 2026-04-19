from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo
import structlog

from coach import memory, prompts
from coach.storage import activities, messages

log = structlog.get_logger()

LlmChat = Callable[..., tuple[str, list[dict]]]
NotifySend = Callable[..., Awaitable[None]]


def _system_prompt(memory_dir: Path, prompts_dir: Path) -> str:
    return prompts.assemble_system_prompt(
        coach_voice=(prompts_dir / "coach_voice.md").read_text(),
        training_plan=memory.read_training_plan(memory_dir),
        athlete_context=memory.read_athlete_context(memory_dir),
    )


async def morning_checkin(*, db_path: Path, memory_dir: Path,
                          prompts_dir: Path, athlete_id: str, model: str,
                          trigger: str, llm_chat: LlmChat,
                          notify_send: NotifySend,
                          tz: str = "America/New_York",
                          now: Callable[[], datetime] | None = None) -> int:
    now_fn = now or (lambda: datetime.now(timezone.utc))
    local = now_fn().astimezone(ZoneInfo(tz))
    today = local.strftime("%A, %B %d")
    recent = activities.recent(db_path, athlete_id)
    system = _system_prompt(memory_dir, prompts_dir)
    user = prompts.build_morning_prompt(
        system_prompt="", today_label=today, recent=recent
    ).split("\n\n---\n\n", 1)[1]

    response, tool_calls = llm_chat(
        model=model, system_prompt=system, user_prompt=user,
        on_observation=lambda t: memory.append_observation(memory_dir, t),
        max_tool_calls=5)

    msg_id = messages.save(db_path, kind="morning", trigger=trigger,
        activity_id=None, model=model, prompt=system + "\n\n---\n\n" + user,
        response=response, tool_calls=tool_calls or None)
    await notify_send(title="Morning check-in", body=response)
    log.info("job.morning", msg_id=msg_id, trigger=trigger,
             tool_calls=len(tool_calls))
    return msg_id


async def post_run_review(*, db_path: Path, memory_dir: Path,
                          prompts_dir: Path, athlete_id: str, model: str,
                          trigger: str, activity_id: int,
                          llm_chat: LlmChat,
                          notify_send: NotifySend) -> int | None:
    if messages.exists_for_activity(db_path, activity_id):
        log.info("job.post_run.skip_duplicate", activity_id=activity_id)
        return None
    act = activities.get(db_path, activity_id)
    if act is None:
        log.warning("job.post_run.unknown_activity", activity_id=activity_id)
        return None

    recent = activities.recent(db_path, athlete_id)
    system = _system_prompt(memory_dir, prompts_dir)
    user = prompts.build_post_run_prompt(
        system_prompt="", activity=act, recent=recent
    ).split("\n\n---\n\n", 1)[1]

    response, tool_calls = llm_chat(
        model=model, system_prompt=system, user_prompt=user,
        on_observation=lambda t: memory.append_observation(memory_dir, t),
        max_tool_calls=5)

    msg_id = messages.save(db_path, kind="post_run", trigger=trigger,
        activity_id=activity_id, model=model,
        prompt=system + "\n\n---\n\n" + user,
        response=response, tool_calls=tool_calls or None)
    await notify_send(title=f"Post-run: {act['name']}", body=response)
    log.info("job.post_run", msg_id=msg_id, activity_id=activity_id,
             trigger=trigger, tool_calls=len(tool_calls))
    return msg_id
