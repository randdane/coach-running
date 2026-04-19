from types import SimpleNamespace
import pytest
from coach.llm import chat, SAVE_OBSERVATION_TOOL


class FakeClient:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.scripted.pop(0)


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(message=SimpleNamespace(
        content=content, tool_calls=tool_calls))


def _resp(content=None, tool_calls=None):
    return SimpleNamespace(choices=[_msg(content, tool_calls)])


def _tc(tid, text):
    return SimpleNamespace(
        id=tid,
        function=SimpleNamespace(
            name="save_observation",
            arguments=f'{{"text": "{text}"}}'))


def test_chat_returns_content_when_no_tool_call():
    client = FakeClient([_resp(content="hi there")])
    saved = []
    out, tool_calls = chat(client, model="m", system_prompt="S",
        user_prompt="U", on_observation=saved.append, max_tool_calls=3)
    assert out == "hi there"
    assert tool_calls == []
    assert saved == []


def test_chat_invokes_save_observation_and_loops():
    client = FakeClient([
        _resp(tool_calls=[_tc("c1", "learned X")]),
        _resp(content="final"),
    ])
    saved = []
    out, tool_calls = chat(client, model="m", system_prompt="S",
        user_prompt="U", on_observation=saved.append, max_tool_calls=3)
    assert out == "final"
    assert saved == ["learned X"]
    assert tool_calls == [{"text": "learned X"}]


def test_chat_honors_max_tool_calls():
    # simulate a runaway: always returns a tool call
    scripted = [_resp(tool_calls=[_tc(f"c{i}", f"o{i}")]) for i in range(10)]
    client = FakeClient(scripted)
    saved = []
    out, tool_calls = chat(client, model="m", system_prompt="S",
        user_prompt="U", on_observation=saved.append, max_tool_calls=2)
    assert len(saved) == 2
    assert out == ""  # no content produced within the cap


def test_save_observation_tool_schema_shape():
    assert SAVE_OBSERVATION_TOOL["function"]["name"] == "save_observation"
    assert "text" in SAVE_OBSERVATION_TOOL["function"]["parameters"]["properties"]
