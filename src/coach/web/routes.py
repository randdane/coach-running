from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import httpx
import structlog

from coach import memory
from coach.config import Settings
from coach.jobs import morning_checkin, post_run_review
from coach.storage import activities, messages, tokens
from coach import llm, notify as notifier

log = structlog.get_logger()

TEMPLATES_DIR = Path(__file__).parent / "templates"


def build_router(settings: Settings, scheduler=None) -> APIRouter:
    router = APIRouter()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def _chat():
        client = llm.make_client(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_master_key)
        def inner(**kw):
            return llm.chat(client, **kw)
        return inner

    async def _notify(title, body):
        await notifier.send(
            base_url=settings.ntfy_base_url, topic=settings.ntfy_topic,
            title=title, body=body)

    def _models() -> list[str]:
        try:
            r = httpx.get(f"{settings.litellm_base_url}/v1/models",
                          headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                          timeout=5)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        except Exception as e:
            log.info("litellm.models_unavailable", error=str(e))
            return [settings.coach_model]

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        today = datetime.now(ZoneInfo(settings.tz)).strftime("%A, %B %d")
        latest = messages.latest(settings.db_path)
        recent = activities.recent(settings.db_path, settings.athlete_id)
        return templates.TemplateResponse(request, "dashboard.html", {
            "today": today, "latest": latest,
            "recent": recent, "models": _models(),
            "default_model": settings.coach_model})

    @router.get("/messages", response_class=HTMLResponse)
    async def messages_page(request: Request):
        return templates.TemplateResponse(request, "messages.html", {
            "messages": messages.list_recent(settings.db_path, limit=100)})

    @router.get("/messages/{mid}", response_class=HTMLResponse)
    async def message_detail(mid: int, request: Request):
        return templates.TemplateResponse(request, "message_detail.html", {
            "message": messages.get(settings.db_path, mid)})

    @router.get("/memory", response_class=HTMLResponse)
    async def memory_page(request: Request):
        return templates.TemplateResponse(request, "memory.html", {
            "content": memory.read_athlete_context(settings.memory_dir)})

    @router.post("/memory")
    async def save_memory(content: str = Form(...)):
        memory.save_athlete_context(settings.memory_dir, content)
        return RedirectResponse("/memory", status_code=303)

    @router.get("/plan", response_class=HTMLResponse)
    async def plan_page(request: Request):
        return templates.TemplateResponse(request, "plan.html", {
            "content": memory.read_training_plan(settings.memory_dir)})

    @router.post("/plan")
    async def save_plan(content: str = Form(...)):
        memory.save_training_plan(settings.memory_dir, content)
        return RedirectResponse("/plan", status_code=303)

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        t = tokens.get(settings.db_path, settings.athlete_id)
        info = {
            "model": settings.coach_model,
            "morning_cron": settings.morning_cron,
            "poll_cron": settings.poll_cron,
            "strava_token_expires": t["expires_at"] if t else "unset",
            "memory_size_bytes": memory.context_size_bytes(settings.memory_dir),
            "memory_warn_threshold_kb": settings.memory_size_warn_kb,
        }
        if scheduler is not None:
            info["next_morning"] = str(scheduler.get_job("morning").next_run_time)
            info["next_poll"] = str(scheduler.get_job("poll").next_run_time)
        return templates.TemplateResponse(request, "settings.html", {"info": info})

    @router.post("/api/jobs/morning", response_class=HTMLResponse)
    async def trigger_morning(model: str = Form(None)):
        chosen = model or settings.coach_model
        mid = await morning_checkin(
            db_path=settings.db_path, memory_dir=settings.memory_dir,
            prompts_dir=Path("prompts"), athlete_id=settings.athlete_id,
            model=chosen, trigger="manual",
            llm_chat=_chat(), notify_send=_notify, tz=settings.tz)
        return HTMLResponse(f"Saved message #{mid}")

    @router.post("/api/jobs/post-run/{activity_id}", response_class=HTMLResponse)
    async def trigger_post_run(activity_id: int):
        mid = await post_run_review(
            db_path=settings.db_path, memory_dir=settings.memory_dir,
            prompts_dir=Path("prompts"), athlete_id=settings.athlete_id,
            model=settings.coach_model, trigger="manual",
            activity_id=activity_id,
            llm_chat=_chat(), notify_send=_notify)
        return HTMLResponse(f"Saved message #{mid}" if mid
                            else "No message (duplicate or unknown activity)")

    return router
