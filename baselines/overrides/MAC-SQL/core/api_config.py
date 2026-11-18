"""
PORTED for this study (2026-08).

Upstream MAC-SQL targets Azure OpenAI through the pre-1.0 `openai` SDK
(`openai.api_type = "azure"`, `openai.ChatCompletion.create(engine=...)`).
That module-level API was removed in openai>=1.0, and this environment runs
openai 3.3.1 against the standard (non-Azure) endpoint, so the config is
rewritten to build a modern client instead.

Only transport is changed. Prompts, agent logic, and control flow are upstream.
"""

import os

from openai import OpenAI

# Model used by every MAC-SQL agent. Kept aligned across systems so
# cross-system comparisons are not confounded by the LLM.
MODEL_NAME = os.getenv("MACSQL_MODEL", "gpt-4o-mini")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def provider_for(model: str) -> str:
    """PORTED (2026-08): route by model-id shape.

    OpenRouter ids are namespaced ("openai/gpt-5.6-luna"); a bare id
    ("gpt-4o") goes to OpenAI directly. Routing on the id rather than on a
    global switch lets one run use OpenRouter while another uses OpenAI
    without editing config, and keeps the two keys separate.
    """
    return "openrouter" if "/" in (model or "") else "openai"


def bare_model(model: str) -> str:
    """Model id without its provider namespace, for capability checks."""
    return (model or "").split("/")[-1]


if provider_for(MODEL_NAME) == "openrouter":
    OPENAI_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENAI_API_BASE = os.getenv("OPENAI_API_BASE") or OPENROUTER_BASE
else:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_API_BASE = os.getenv("OPENAI_API_BASE") or None

_client_kwargs = {"api_key": OPENAI_API_KEY}
if OPENAI_API_BASE:
    _client_kwargs["base_url"] = OPENAI_API_BASE

client = OpenAI(**_client_kwargs)
