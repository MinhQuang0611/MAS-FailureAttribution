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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE") or None

# Model used by every MAC-SQL agent. Kept aligned with the nlsql pipeline's
# backbone so cross-system comparisons are not confounded by the LLM.
MODEL_NAME = os.getenv("MACSQL_MODEL", "gpt-4o")

_client_kwargs = {"api_key": OPENAI_API_KEY}
if OPENAI_API_BASE:
    _client_kwargs["base_url"] = OPENAI_API_BASE

client = OpenAI(**_client_kwargs)
