"""Naming the model the way the harness's own client needs it (PLAN §11, 4.1).

Both container harnesses reach the endpoint through litellm, so this lives on its
own instead of inside one of them.
"""

from __future__ import annotations


def litellm_model(model: str) -> str:
    """Prefix a bare model name with the provider litellm should call.

    litellm refuses a bare ``deepseek-v4.1-flash`` with "LLM Provider NOT provided"
    because it cannot tell which API to call. Our endpoint is OpenAI-compatible, so
    an unprefixed name means ``openai/``; a name that already carries a provider is
    left as it is.
    """

    if not model:
        return ""
    return model if "/" in model else f"openai/{model}"


__all__ = ["litellm_model"]
