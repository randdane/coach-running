import hmac
from typing import Callable
from fastapi import APIRouter, HTTPException, Request
import structlog

log = structlog.get_logger()


def build_router(*, secret: str, athlete_id: str,
                 on_create: Callable[[str, int], bool]) -> APIRouter:
    router = APIRouter()

    def _check(path_secret: str) -> None:
        if not hmac.compare_digest(path_secret, secret):
            raise HTTPException(status_code=404)

    @router.get("/webhook/strava/{path_secret}")
    async def handshake(path_secret: str, request: Request):
        _check(path_secret)
        params = request.query_params
        return {"hub.challenge": params.get("hub.challenge", "")}

    @router.post("/webhook/strava/{path_secret}")
    async def create(path_secret: str, request: Request):
        _check(path_secret)
        body = await request.json()
        if body.get("object_type") != "activity":
            return {"status": "ignored"}
        if body.get("aspect_type") != "create":
            return {"status": "ignored"}
        if str(body.get("owner_id")) != str(athlete_id):
            log.info("webhook.wrong_owner", owner=body.get("owner_id"))
            return {"status": "ignored"}
        activity_id = int(body["object_id"])
        scheduled = on_create("webhook", activity_id)
        return {"status": "ok" if scheduled else "duplicate"}

    return router
