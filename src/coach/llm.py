import json
from typing import Callable, Protocol
from openai import OpenAI


SAVE_OBSERVATION_TOOL = {
    "type": "function",
    "function": {
        "name": "save_observation",
        "description": "Save a durable observation about the athlete to long-term memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The observation to save."}
            },
            "required": ["text"],
        },
    },
}


class _ChatClient(Protocol):
    chat: object  # openai-compatible


def make_client(*, base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key)


def chat(client: _ChatClient, *, model: str, system_prompt: str,
         user_prompt: str, on_observation: Callable[[str], None],
         max_tool_calls: int = 5) -> tuple[str, list[dict]]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    tool_args: list[dict] = []
    calls_made = 0

    while True:
        if calls_made >= max_tool_calls and tool_args:
            return "", tool_args

        resp = client.chat.completions.create(
            model=model, messages=messages, tools=[SAVE_OBSERVATION_TOOL])
        msg = resp.choices[0].message

        if not msg.tool_calls:
            return msg.content or "", tool_args

        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name,
                              "arguments": c.function.arguments}}
                for c in msg.tool_calls
            ],
        })

        for call in msg.tool_calls:
            if calls_made >= max_tool_calls:
                break
            if call.function.name != "save_observation":
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": "unknown tool"})
                continue
            try:
                args = json.loads(call.function.arguments)
            except (json.JSONDecodeError, KeyError):
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": "Error: malformed arguments."})
                continue
            on_observation(args["text"])
            tool_args.append(args)
            calls_made += 1
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": "Saved."})
