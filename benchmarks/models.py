"""Model identifiers for the answer and judge calls.

Models are written as "provider/model", for example `openai/gpt-4o` or
`anthropic/claude-sonnet-4-5`. The provider prefix selects the LiteLLM route
and decides which API key must be present. Pin a dated model snapshot where
one exists: an alias floats to whatever the provider points it at, and the
resolved id is what a run's manifest should record.

The judge needs a model that answers within max_tokens=10. A reasoning model
can spend that budget on reasoning and return an empty string, which the
runner treats as an error row rather than as "wrong".
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

DEFAULT_ANSWER_MODEL = "gemini/gemini-3.8-flash"
DEFAULT_JUDGE_MODEL = "openai/gpt-4o"

CHUNK_MODES = ("with", "without")

# The API key each provider needs. Any provider not listed here is passed
# through to LiteLLM, which reads its own variables; use --answer-api-base /
# --judge-api-base with the `openai` prefix for an OpenAI-compatible endpoint.
KEY_ENV: Dict[str, Tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


def split_model(spec: str) -> Tuple[str, str]:
    """"provider/model" -> (provider, model). Rejects a bare model name."""
    provider, sep, model = (spec or "").partition("/")
    if not sep or not provider or not model:
        raise ValueError(
            f"model {spec!r} must be written provider/model, "
            "e.g. openai/gpt-4o or anthropic/claude-sonnet-4-5")
    return provider.lower(), model


def missing_key(spec: str, api_base: Optional[str] = None) -> Optional[str]:
    """Name the env var to set if `spec` cannot authenticate, else None."""
    provider, _ = split_model(spec)
    needed = KEY_ENV.get(provider)
    if not needed or (api_base and provider == "openai"
                      and os.environ.get("LLM_API_KEY")):
        return None
    if any(os.environ.get(k) for k in needed):
        return None
    return " or ".join(needed)
