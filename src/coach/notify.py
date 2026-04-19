import httpx
import structlog

log = structlog.get_logger()


async def send(*, base_url: str, topic: str, title: str, body: str) -> None:
    url = f"{base_url.rstrip('/')}/{topic}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, content=body.encode("utf-8"),
                             headers={"title": title})
            if r.status_code >= 400:
                log.warning("ntfy.non_2xx", status=r.status_code, body=r.text[:200])
    except httpx.HTTPError as e:
        log.warning("ntfy.failed", error=str(e))
