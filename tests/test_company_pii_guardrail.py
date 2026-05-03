"""Tests for CompanyPIIGuardrail (block + redact)."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from guardrails.guardrails import CompanyPIIGuardrail


def _run(data: dict) -> dict:
    g = CompanyPIIGuardrail()
    return asyncio.run(g.async_pre_call_hook(None, None, data, "completion"))


def _msg(content, role: str = "user") -> dict:
    return {"messages": [{"role": role, "content": content}]}


# ---------------------------------------------------------------------------
# BLOCK: SSN
# ---------------------------------------------------------------------------


class TestSSNBlock:
    def test_blocks_valid_ssn(self):
        with pytest.raises(HTTPException) as exc:
            _run(_msg("My SSN is 123-45-6789"))
        assert exc.value.status_code == 400
        assert "SSN" in exc.value.detail

    def test_does_not_echo_ssn_in_detail(self):
        with pytest.raises(HTTPException) as exc:
            _run(_msg("123-45-6789"))
        assert "123-45-6789" not in exc.value.detail

    def test_unformatted_9_digits_not_blocked(self):
        # 9 digits with no dashes — not SSN format, also too short for CC.
        result = _run(_msg("number 123456789"))
        assert result["messages"][0]["content"] == "number 123456789"

    def test_ssn_inside_anthropic_blocks_blocks(self):
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "ssn 123-45-6789"}],
                }
            ]
        }
        with pytest.raises(HTTPException):
            _run(data)


# ---------------------------------------------------------------------------
# BLOCK: credit card (Luhn-validated)
# ---------------------------------------------------------------------------


# Known-valid Luhn test number (Visa).
VALID_CC = "4532015112830366"
INVALID_CC = "1234567890123456"


class TestCreditCardBlock:
    def test_blocks_valid_luhn(self):
        with pytest.raises(HTTPException) as exc:
            _run(_msg(f"Card: {VALID_CC}"))
        assert exc.value.status_code == 400
        assert "CREDIT_CARD" in exc.value.detail

    def test_blocks_with_dashes(self):
        spaced = "-".join([VALID_CC[i : i + 4] for i in range(0, 16, 4)])
        with pytest.raises(HTTPException):
            _run(_msg(f"Card: {spaced}"))

    def test_blocks_with_spaces(self):
        spaced = " ".join([VALID_CC[i : i + 4] for i in range(0, 16, 4)])
        with pytest.raises(HTTPException):
            _run(_msg(f"Card: {spaced}"))

    def test_does_not_block_invalid_luhn(self):
        result = _run(_msg(f"Card: {INVALID_CC}"))
        # Passes through untouched (no other rules match a 16-digit string).
        assert INVALID_CC in result["messages"][0]["content"]

    def test_does_not_echo_card_in_detail(self):
        with pytest.raises(HTTPException) as exc:
            _run(_msg(f"Card {VALID_CC}"))
        assert VALID_CC not in exc.value.detail

    def test_short_digit_string_not_treated_as_cc(self):
        result = _run(_msg("order #12345"))
        assert result["messages"][0]["content"] == "order #12345"


# ---------------------------------------------------------------------------
# REDACT: email
# ---------------------------------------------------------------------------


class TestEmailRedaction:
    @pytest.mark.parametrize(
        "email",
        [
            "john@example.com",
            "john.doe@example.co.uk",
            "john+tag@example.com",
            "user_name@sub.example.org",
        ],
    )
    def test_redacts_email(self, email):
        result = _run(_msg(f"Reach me at {email}"))
        out = result["messages"][0]["content"]
        assert "[REDACTED_EMAIL]" in out
        assert email not in out


# ---------------------------------------------------------------------------
# REDACT: phone
# ---------------------------------------------------------------------------


class TestPhoneRedaction:
    @pytest.mark.parametrize(
        "phone",
        [
            "555-123-4567",
            "(555) 123-4567",
            "+1-555-123-4567",
            "555.123.4567",
            "5551234567",
        ],
    )
    def test_redacts_phone(self, phone):
        result = _run(_msg(f"Call me on {phone}"))
        assert "[REDACTED_PHONE]" in result["messages"][0]["content"]


# ---------------------------------------------------------------------------
# REDACT: API keys
# ---------------------------------------------------------------------------


class TestApiKeyRedaction:
    def test_redacts_long_sk_key(self):
        key = "sk-" + "a" * 40
        result = _run(_msg(f"key={key}"))
        out = result["messages"][0]["content"]
        assert "[REDACTED_API_KEY]" in out
        assert key not in out

    def test_short_sk_string_not_redacted(self):
        result = _run(_msg("sk-short"))
        assert "[REDACTED_API_KEY]" not in result["messages"][0]["content"]


# ---------------------------------------------------------------------------
# REDACT: TSMC company mentions
# ---------------------------------------------------------------------------


class TestTsmcRedaction:
    @pytest.mark.parametrize(
        "text",
        [
            "I work at tsmc",
            "I work at TSMC",
            "I work at TsMc",
        ],
    )
    def test_redacts_bare_token_case_insensitive(self, text):
        out = _run(_msg(text))["messages"][0]["content"]
        assert "[REDACTED_COMPANY]" in out
        assert "tsmc" not in out.lower().replace("[redacted_company]", "")

    def test_redacts_dotcom_domain(self):
        out = _run(_msg("Visit tsmc.com today"))["messages"][0]["content"]
        assert "[REDACTED_COMPANY]" in out
        assert "tsmc.com" not in out.lower()

    @pytest.mark.parametrize(
        "token", ["xtsmcy", "tsmc123", "internal-tsmc-alias", "TSMCFAB18"]
    )
    def test_redacts_tokens_containing_tsmc(self, token):
        out = _run(_msg(f"alias {token} resolves"))["messages"][0]["content"]
        assert "[REDACTED_COMPANY]" in out
        assert token.lower() not in out.lower()

    def test_passes_text_without_tsmc(self):
        out = _run(_msg("Hello world"))["messages"][0]["content"]
        assert "[REDACTED_COMPANY]" not in out


# ---------------------------------------------------------------------------
# REDACT: names
# ---------------------------------------------------------------------------


class TestNameRedaction:
    @pytest.mark.parametrize(
        "text",
        [
            "Mr. Smith called",
            "Mrs. Johnson said hi",
            "Dr Jane Doe approved it",
            "Prof. Alan Turing wrote a paper",
            "Ms Elizabeth Warren attended",
        ],
    )
    def test_redacts_title_prefixed_names(self, text):
        out = _run(_msg(text))["messages"][0]["content"]
        assert "[REDACTED_NAME]" in out

    def test_redacts_two_consecutive_capitalized_words(self):
        out = _run(_msg("John Smith called"))["messages"][0]["content"]
        assert "[REDACTED_NAME]" in out
        assert "John Smith" not in out

    def test_does_not_redact_single_capitalized_word(self):
        # Only one capitalized word ("Hello") — no name pattern matches.
        out = _run(_msg("Hello there friend"))["messages"][0]["content"]
        assert "[REDACTED_NAME]" not in out

    def test_does_not_redact_lowercase_words(self):
        out = _run(_msg("the quick brown fox"))["messages"][0]["content"]
        assert "[REDACTED_NAME]" not in out


# ---------------------------------------------------------------------------
# Content shapes / structural edge cases
# ---------------------------------------------------------------------------


class TestContentShapes:
    def test_anthropic_text_block_redacted(self):
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "email: x@y.com"},
                        {"type": "image", "source": {"data": "..."}},
                    ],
                }
            ]
        }
        out = _run(data)["messages"][0]["content"]
        assert out[0]["text"] == "email: [REDACTED_EMAIL]"
        # Non-text block is untouched.
        assert out[1] == {"type": "image", "source": {"data": "..."}}

    def test_string_content_returns_string(self):
        out = _run(_msg("plain string content"))["messages"][0]["content"]
        assert isinstance(out, str)

    def test_no_messages_key(self):
        result = _run({})
        assert result == {}

    def test_empty_messages_list(self):
        result = _run({"messages": []})
        assert result == {"messages": []}

    def test_clean_text_passes_unmodified(self):
        clean = "Hello, this is a normal request about Python sorting."
        out = _run(_msg(clean))["messages"][0]["content"]
        # "Python sorting" is two capitalized-then-lower; "sorting" is lowercase
        # so won't trigger NAME_FULL_RE. Confirms the heuristic is bounded.
        assert out == clean

    def test_message_without_content_field(self):
        # Should not crash on missing content.
        data = {"messages": [{"role": "user"}]}
        result = _run(data)
        assert result == data


# ---------------------------------------------------------------------------
# Pipeline ordering: BLOCK runs across all messages before any REDACT.
# ---------------------------------------------------------------------------


class TestPipelineOrder:
    def test_block_in_later_message_prevents_redact_of_earlier(self):
        data = {
            "messages": [
                {"role": "user", "content": "email me at a@b.com"},
                {"role": "user", "content": "ssn 123-45-6789"},
            ]
        }
        with pytest.raises(HTTPException):
            _run(data)
        # The original first message must NOT have been redacted, because
        # block runs first and aborts before any rewrite.
        assert data["messages"][0]["content"] == "email me at a@b.com"

    def test_multiple_redactions_in_one_message(self):
        text = "Mr. Smith at john@example.com called from 555-123-4567 about tsmc"
        out = _run(_msg(text))["messages"][0]["content"]
        assert "[REDACTED_NAME]" in out
        assert "[REDACTED_EMAIL]" in out
        assert "[REDACTED_PHONE]" in out
        assert "[REDACTED_COMPANY]" in out
        assert "Smith" not in out
        assert "john@example.com" not in out
        assert "tsmc" not in out.lower().replace("[redacted_company]", "")
