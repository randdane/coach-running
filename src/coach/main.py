from contextlib import asynccontextmanager
from pathlib import Path
import structlog
import uvicorn
from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from coach.config import get_settings
from coach.scheduler import build_scheduler, schedule_post_run
from coach.storage.db import apply_migrations
from coach.strava.webhook import build_router as build_webhook_router
from coach.web.routes import build_router as build_web_router

log = structlog.get_logger()


def create_app(*, start_scheduler: bool = True) -> FastAPI:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(settings.db_path)

    sched = build_scheduler(settings) if start_scheduler else None

    def _on_create(trigger: str, activity_id: int) -> bool:
        if sched is None:
            return False
        return schedule_post_run(sched, settings, trigger, activity_id)

    limiter = Limiter(key_func=get_remote_address,
                      default_limits=[settings.webhook_rate_limit])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if sched is not None:
            sched.start()
        yield
        if sched is not None:
            sched.shutdown(wait=False)

    app = FastAPI(lifespan=lifespan)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    webhook_router = build_webhook_router(
        secret=settings.webhook_secret,
        athlete_id=settings.athlete_id,
        on_create=_on_create)
    for route in webhook_router.routes:
        route.dependant.dependencies  # noqa: B018
    app.include_router(webhook_router)
    app.include_router(build_web_router(settings, scheduler=sched))

    @app.get("/healthz")
    async def healthz():
        try:
            from coach.storage.db import connect
            with connect(settings.db_path) as c:
                c.execute("SELECT 1").fetchone()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/readyz")
    async def readyz():
        from coach.storage import tokens
        t = tokens.get(settings.db_path, settings.athlete_id)
        return {"ok": True, "token_loaded": t is not None}

    return app


def main():
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ])
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
