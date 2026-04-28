"""Prompt-security middleware.

Evaluates the inbound Anthropic Messages payload against a set of rules
loaded from YAML. On a block, raises HTTPException(400) so the gateway
never forwards the prompt to the backend.

Rule types are pluggable: add a new dataclass implementing the `Rule`
protocol and a branch in `_build_rule`. Today: regex-based blocking.
Tomorrow: allowlist patterns, secret-shape detection, classifier-based
checks, etc.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

import yaml
from fastapi import HTTPException, status

from app.adapters.anthropic import content_to_text
from app.middleware.auth import Client

logger = logging.getLogger(__name__)


class SecurityViolation(HTTPException):
    def __init__(self, rule_name: str, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "prompt_blocked", "rule": rule_name, "reason": detail},
        )


class Rule(Protocol):
    name: str

    def evaluate(self, texts: list[str]) -> str | None:
        """Return a non-empty reason string to block, None to allow."""


@dataclass(frozen=True)
class RegexBlockRule:
    name: str
    patterns: tuple[re.Pattern[str], ...]

    def evaluate(self, texts: list[str]) -> str | None:
        for text in texts:
            for pat in self.patterns:
                if pat.search(text):
                    return f"matched pattern /{pat.pattern}/"
        return None


def _build_rule(raw: dict) -> Rule:
    rtype = raw.get("type", "regex")
    if rtype == "regex":
        flags = re.IGNORECASE if raw.get("ignore_case") else 0
        patterns = tuple(re.compile(p, flags) for p in raw.get("patterns", []))
        return RegexBlockRule(name=raw["name"], patterns=patterns)
    raise ValueError(f"unknown security rule type: {rtype}")


class SecurityEngine:
    def __init__(self, rules: Iterable[Rule]) -> None:
        self._rules: tuple[Rule, ...] = tuple(rules)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SecurityEngine":
        p = Path(path)
        if not p.exists():
            logger.warning("security config %s not found; no prompt rules active", p)
            return cls([])
        raw = yaml.safe_load(p.read_text()) or {}
        rules = [_build_rule(r) for r in raw.get("rules", [])]
        logger.info("loaded %d security rule(s) from %s", len(rules), p)
        return cls(rules)

    def __len__(self) -> int:
        return len(self._rules)

    async def check(self, payload: dict, client: Client) -> None:
        if not self._rules:
            return
        texts = _extract_texts(payload)
        for rule in self._rules:
            reason = rule.evaluate(texts)
            if reason:
                logger.warning(
                    "prompt blocked: client=%s rule=%s reason=%s",
                    client.name, rule.name, reason,
                )
                raise SecurityViolation(rule.name, reason)


def _extract_texts(payload: dict) -> list[str]:
    texts: list[str] = []
    sys = payload.get("system")
    if sys:
        texts.append(content_to_text(sys))
    for m in payload.get("messages", []):
        texts.append(content_to_text(m.get("content", "")))
    return texts
