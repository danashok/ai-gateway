"""
Company PII Guardrail for LiteLLM Proxy.

This guardrail runs as a `pre_call` hook on every chat-completions
request. It walks each message's text content (handling both plain
strings and Anthropic-style `[{"type":"text","text":"..."}]` content
blocks) and applies two classes of rule:

- BLOCK rules raise HTTPException 400 — the request never reaches the
  upstream model. Currently: US SSNs and credit-card numbers
  (Luhn-validated to cut down on false positives).
- REDACT rules rewrite the prompt in place — the request still goes
  through, but the matched substring is replaced with a placeholder
  before the prompt is forwarded. Currently: email addresses,
  US-ish phone numbers, and `sk-`-prefixed API-key-shaped strings.

Adding a new pattern:
- BLOCK: add a new check inside `_check_block`. Raise
  `HTTPException(status_code=400, ...)` with a generic detail string
  that does NOT echo the matched content back.
- REDACT: add a new compiled regex at module scope and a new
  `.sub(...)` call inside `_redact`.

Failure mode: this guardrail is a security control. If regex
evaluation itself raises (or any other unexpected error happens
inside the hook), we fail closed by returning HTTP 500 rather than
letting the prompt through unchecked.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import HTTPException
from litellm.integrations.custom_guardrail import CustomGuardrail

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CC_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE_RE = re.compile(r"\b\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
APIKEY_RE = re.compile(r"sk-[A-Za-z0-9]{32,}")


def luhn(card_number: str) -> bool:
    """Validate a credit-card-shaped string using the Luhn checksum.

    Strips spaces and dashes first. Returns False for strings that are
    too short, too long, or contain non-digits after stripping.
    """
    digits = re.sub(r"[ -]", "", card_number)
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


class CompanyPIIGuardrail(CustomGuardrail):
    """Pre-call guardrail enforcing company PII policy on prompts."""

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> dict:
        try:
            messages = data.get("messages") or []

            for msg in messages:
                content = msg.get("content")
                if isinstance(content, str):
                    self._check_block(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            self._check_block(block.get("text", ""))

            for idx, msg in enumerate(messages):
                role = msg.get("role")
                content = msg.get("content")
                if isinstance(content, str):
                    redacted = self._redact(content)
                    self._log_redact(idx, role, content, redacted)
                    msg["content"] = redacted
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            before = block.get("text", "")
                            after = self._redact(before)
                            self._log_redact(idx, role, before, after)
                            block["text"] = after

            return data

        except HTTPException:
            raise
        except Exception:
            # we fail closed because this is a security control: a bug
            # in rule evaluation must not result in unfiltered prompts
            # reaching the upstream model.
            logger.exception("CompanyPIIGuardrail failed unexpectedly")
            raise HTTPException(
                status_code=500,
                detail="Guardrail evaluation failed",
            )

    @staticmethod
    def _check_block(text: str) -> None:
        if SSN_RE.search(text):
            raise HTTPException(
                status_code=400,
                detail="Prompt contains restricted content (type: SSN)",
            )
        for match in CC_RE.finditer(text):
            if luhn(match.group(0)):
                raise HTTPException(
                    status_code=400,
                    detail="Prompt contains restricted content (type: CREDIT_CARD)",
                )

    @staticmethod
    def _redact(text: str) -> str:
        text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
        text = PHONE_RE.sub("[REDACTED_PHONE]", text)
        text = APIKEY_RE.sub("[REDACTED_API_KEY]", text)
        return text

    @staticmethod
    def _log_redact(idx: int, role: str | None, before: str, after: str) -> None:
        changed = "REDACTED" if before != after else "unchanged"
        logger.info(
            "[guardrail] msg[%d] role=%s %s\n  before: %r\n  after : %r",
            idx,
            role,
            changed,
            before,
            after,
        )
