"""Translate between Anthropic Messages API and OpenAI Chat Completions API.

MVP scope: text-only messages, system prompt, streaming. Tool-use and image
content blocks are not yet translated and will be ignored on input / absent
on output.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def anthropic_to_openai_messages(payload: dict) -> list[dict]:
    messages: list[dict] = []

    system = payload.get("system")
    if system:
        messages.append({"role": "system", "content": content_to_text(system)})

    for msg in payload.get("messages", []):
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        messages.append({"role": role, "content": content_to_text(msg.get("content", ""))})

    return messages


def _new_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _map_stop_reason(finish: str | None) -> str | None:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "stop_sequence",
    }.get(finish or "", "end_turn")


def openai_to_anthropic_response(resp: Any, model_hint: str = "") -> dict:
    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    finish_reason = choice.get("finish_reason")

    usage = data.get("usage") or {}
    return {
        "id": _new_id(),
        "type": "message",
        "role": "assistant",
        "model": model_hint or data.get("model", ""),
        "content": [{"type": "text", "text": text}],
        "stop_reason": _map_stop_reason(finish_reason),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def openai_stream_to_anthropic_events(
    stream: AsyncIterator[Any],
    model_hint: str = "",
) -> AsyncIterator[bytes]:
    msg_id = _new_id()
    block_open = False
    finish_reason: str | None = None
    input_tokens = 0
    output_tokens = 0

    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": model_hint,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })

    async for chunk in stream:
        data = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
        choices = data.get("choices") or []
        if not choices:
            continue

        delta = choices[0].get("delta") or {}
        text = delta.get("content")
        if choices[0].get("finish_reason"):
            finish_reason = choices[0]["finish_reason"]

        usage = data.get("usage")
        if usage:
            input_tokens = usage.get("prompt_tokens", input_tokens)
            output_tokens = usage.get("completion_tokens", output_tokens)

        if text:
            if not block_open:
                yield _sse("content_block_start", {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                })
                block_open = True
            yield _sse("content_block_delta", {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            })

    if block_open:
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})

    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": _map_stop_reason(finish_reason), "stop_sequence": None},
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    })
    yield _sse("message_stop", {"type": "message_stop"})
