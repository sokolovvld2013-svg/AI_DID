"""Тесты классификации ошибок LLM в core.llm_errors."""

from __future__ import annotations

import pytest

from core.llm_errors import LLMUserFacingError, friendly_llm_error_message, llm_error_code


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("max_tokens_per_request exceeded", "LLM_CONTEXT_TOO_LONG"),
        ("requested 9000 tokens but max 8192 tokens", "LLM_CONTEXT_TOO_LONG"),
        ("maximum context length is 128000", "LLM_CONTEXT_TOO_LONG"),
        ("rate limit reached for model", "LLM_RATE_LIMIT"),
        ("HTTP 429 Too Many Requests", "LLM_RATE_LIMIT"),
        ("request timed out after 60s", "LLM_TIMEOUT"),
        ("Invalid API key provided", "LLM_AUTH_ERROR"),
        ("401 Unauthorized", "LLM_AUTH_ERROR"),
        ("Connection refused", "LLM_UNREACHABLE"),
        ("что-то совсем неожиданное", "LLM_ERROR"),
    ],
)
def test_llm_error_code(message: str, code: str) -> None:
    assert llm_error_code(RuntimeError(message)) == code


def test_friendly_message_is_not_empty() -> None:
    message = friendly_llm_error_message(RuntimeError("rate limit"))
    assert message
    assert message != "rate limit"


def test_friendly_message_for_generic_error() -> None:
    assert friendly_llm_error_message(RuntimeError("boom"))


def test_user_facing_error_is_exception() -> None:
    exc = LLMUserFacingError("текст")
    assert isinstance(exc, Exception)