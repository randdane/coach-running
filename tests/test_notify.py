import httpx
import respx
from coach.notify import send


@respx.mock
async def test_send_posts_to_topic():
    route = respx.post("http://ntfy/coach").mock(
        return_value=httpx.Response(200))
    await send(base_url="http://ntfy", topic="coach",
               title="Morning check-in", body="go run")
    assert route.called
    req = route.calls[0].request
    assert req.content.decode() == "go run"
    assert req.headers["title"] == "Morning check-in"


@respx.mock
async def test_send_swallows_errors():
    respx.post("http://ntfy/coach").mock(return_value=httpx.Response(500))
    # should not raise
    await send(base_url="http://ntfy", topic="coach", title="t", body="b")
