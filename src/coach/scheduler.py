from __future__ import annotations
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import structlog
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from coach import jobs, llm, memory, notify as notifier
from coach.config import Settings
from coach.storage import activities as activities_repo
from coach.storage.db import connect

log = structlog.get_logger()


def _llm_chat_factory(settings: Settings):
    client = llm.make_client(
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_master_key)
    def _chat(**kw):
        return llm.chat(client, **kw)
    return _chat


def _notify_factory(settings: Settings):
    async def _send(*, title, body):
        await notifier.send(
            base_url=settings.ntfy_base_url, topic=settings.ntfy_topic,
            title=title, body=body)
    return _send


def job_id_for_activity(activity_id: int) -> str:
    return f"post_run:{activity_id}"


def build_scheduler(settings: Settings) -> AsyncIOScheduler:
    jobstore = SQLAlchemyJobStore(url=f"sqlite:///{settings.data_dir}/scheduler.db")
    sched = AsyncIOScheduler(
        jobstores={"default": jobstore},
        timezone=settings.tz)
    _register_crons(sched, settings)
    return sched


def _register_crons(sched: AsyncIOScheduler, settings: Settings) -> None:
    sched.add_job(_morning_job, CronTrigger.from_crontab(settings.morning_cron,
                  timezone=settings.tz),
                  args=[settings], id="morning", replace_existing=True,
                  misfire_grace_time=3600)
    sched.add_job(_poll_job, CronTrigger.from_crontab(settings.poll_cron,
                  timezone=settings.tz),
                  args=[settings, sched], id="poll", replace_existing=True,
                  misfire_grace_time=3600)
    sched.add_job(_nightly_backup_job, CronTrigger(hour=3, minute=0,
                  timezone=settings.tz),
                  args=[settings], id="backup", replace_existing=True)
    sched.add_job(_memory_size_warn_job, CronTrigger(hour=3, minute=5,
                  timezone=settings.tz),
                  args=[settings], id="memory_warn", replace_existing=True)


async def _morning_job(settings: Settings) -> None:
    await jobs.morning_checkin(
        db_path=settings.db_path, memory_dir=settings.memory_dir,
        prompts_dir=Path("prompts"), athlete_id=settings.athlete_id,
        model=settings.coach_model, trigger="scheduled",
        llm_chat=_llm_chat_factory(settings),
        notify_send=_notify_factory(settings),
        tz=settings.tz)


async def _poll_job(settings: Settings, sched: AsyncIOScheduler) -> None:
    from coach.strava.client import StravaClient
    client = StravaClient(settings.db_path, settings.athlete_id,
        client_id=settings.strava_client_id,
        client_secret=settings.strava_client_secret,
        initial_refresh_token=settings.strava_refresh_token)
    since = activities_repo.most_recent_start_date(settings.db_path,
                settings.athlete_id) or "2026-01-01T00:00:00Z"
    found = await client.list_recent_since(since)
    for act in found:
        activities_repo.upsert(settings.db_path,
            {**act, "athlete_id": settings.athlete_id}, raw=act)
        try:
            sched.add_job(
                _run_post_run, trigger=DateTrigger(
                    run_date=datetime.now(timezone.utc) +
                              timedelta(seconds=settings.webhook_delay_seconds)),
                args=[settings, "poll", act["id"]],
                id=job_id_for_activity(act["id"]),
                replace_existing=False, max_instances=1,
                coalesce=True, misfire_grace_time=3600)
        except Exception as e:
            log.info("poll.skip_existing", activity_id=act["id"], error=str(e))


async def _nightly_backup_job(settings: Settings) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = settings.backups_dir / f"coach-{ts}.tar.gz"
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    tmp_db = settings.backups_dir / f"coach-{ts}.db"
    with connect(settings.db_path) as c:
        c.execute(f"VACUUM INTO '{tmp_db}'")
    with tarfile.open(out, "w:gz") as tar:
        tar.add(tmp_db, arcname=f"coach-{ts}.db")
        tar.add(settings.memory_dir, arcname="memory")
    tmp_db.unlink()
    all_bundles = sorted(settings.backups_dir.glob("coach-*.tar.gz"))
    for extra in all_bundles[:-14]:
        extra.unlink()
    log.info("backup.done", path=str(out))


async def _memory_size_warn_job(settings: Settings) -> None:
    size = memory.context_size_bytes(settings.memory_dir)
    if size > settings.memory_size_warn_kb * 1024:
        log.warning("memory.oversize", size_bytes=size,
                    threshold_kb=settings.memory_size_warn_kb)


async def _run_post_run(settings: Settings, trigger: str, activity_id: int) -> None:
    await jobs.post_run_review(
        db_path=settings.db_path, memory_dir=settings.memory_dir,
        prompts_dir=Path("prompts"), athlete_id=settings.athlete_id,
        model=settings.coach_model, trigger=trigger,
        activity_id=activity_id,
        llm_chat=_llm_chat_factory(settings),
        notify_send=_notify_factory(settings))


def schedule_post_run(sched: AsyncIOScheduler, settings: Settings,
                      trigger: str, activity_id: int) -> bool:
    run_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.webhook_delay_seconds)
    try:
        sched.add_job(
            _run_post_run, trigger=DateTrigger(run_date=run_at),
            args=[settings, trigger, activity_id],
            id=job_id_for_activity(activity_id),
            replace_existing=False, max_instances=1,
            coalesce=True, misfire_grace_time=3600)
        return True
    except Exception as e:
        log.info("schedule.skip_existing", activity_id=activity_id, error=str(e))
        return False
