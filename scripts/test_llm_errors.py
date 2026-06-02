# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.llm_errors import friendly_llm_error_message


class FakeAPIError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.body = {"error": {"message": message, "type": "max_tokens_per_request"}}


msg = friendly_llm_error_message(
    FakeAPIError("Requested 408943 tokens, max 300000 tokens per request")
)
assert "лимит токенов" in msg.lower(), msg
assert "408943" not in msg
print("OK:", msg)
