"""LiteLLM-backed LLM service.

Thin wrapper. Future hooks (prompt-security checks, audit logging, model
routing) should compose around these functions in `app/routers/messages.py`
rather than being baked in here.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

import litellm

from app.adapters.anthropic import (
    anthropic_to_openai_messages,
    openai_stream_to_anthropic_events,
    openai_to_anthropic_response,
)
from app.config import get_settings

logger = logging.getLogger(__name__)


def _backend_kwargs() -> dict:
    settings = get_settings()
    return {
        "model": f"openai/{settings.backend_model}",
        "api_base": settings.backend_api_base,
        "api_key": settings.backend_api_key,
    }


def _common_params(payload: dict) -> dict:
    return {
        "messages": anthropic_to_openai_messages(payload),
        "max_tokens": payload.get("max_tokens"),
        "temperature": payload.get("temperature"),
        "top_p": payload.get("top_p"),
        "stop": payload.get("stop_sequences"),
    }


async def complete_messages(payload: dict) -> dict:
    response = await litellm.acompletion(
        **_backend_kwargs(),
        **_common_params(payload),
        stream=False,
    )
    return openai_to_anthropic_response(response, model_hint=payload.get("model", ""))


async def stream_messages(payload: dict) -> AsyncIterator[bytes]:
    stream = await litellm.acompletion(
        **_backend_kwargs(),
        **_common_params(payload),
        stream=True,
    )
    async for event in openai_stream_to_anthropic_events(
        stream, model_hint=payload.get("model", "")
    ):
        yield event
