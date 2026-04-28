"""Anthropic-compatible /v1/messages endpoint.

Pipeline:
  1. authorize          (api key + source-IP allowlist)
  2. prompt_security    (regex/etc. rules; blocks on match)
  3. forward to backend (LiteLLM)

Future hooks slot in around step 3:
  * post-response audit log -> persist payload + response
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.middleware.auth import Client, authorize
from app.services.llm import complete_messages, stream_messages

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["messages"])


@router.post("/messages")
async def create_message(request: Request, client: Client = Depends(authorize)):
    payload = await request.json()
    stream = bool(payload.get("stream"))
    logger.info("messages: client=%s stream=%s model=%s", client.name, stream, payload.get("model"))

    await request.app.state.security_engine.check(payload, client)
    # TODO(audit): audit.log_request(payload, client)

    if stream:
        return StreamingResponse(
            stream_messages(payload),
            media_type="text/event-stream",
        )
    response = await complete_messages(payload)
    # TODO(audit): audit.log_response(response, client)
    return JSONResponse(response)
