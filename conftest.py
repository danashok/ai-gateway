"""Pytest setup.

The guardrail module imports `litellm.integrations.custom_guardrail` and
`fastapi`. The full proxy stack is heavy (and only present inside the
docker container), so when running tests on a bare host we substitute
minimal stubs. If the real packages are installed they win — the stubs
only fill in the gap.
"""
from __future__ import annotations

import sys
import types


def _stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


try:
    import litellm.integrations.custom_guardrail  # noqa: F401
except ModuleNotFoundError:
    _stub("litellm")
    _stub("litellm.integrations")
    _custom = _stub("litellm.integrations.custom_guardrail")

    class CustomGuardrail:  # noqa: D401 — stub
        def __init__(self, *args, **kwargs):
            pass

    _custom.CustomGuardrail = CustomGuardrail


try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    _fastapi = _stub("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str = ""):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    _fastapi.HTTPException = HTTPException
