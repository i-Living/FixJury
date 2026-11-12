"""OpenAI-compatible chat completions (PLAN §9).

opencode-go (Zen Go) is OpenAI-compatible but rejects requests without the
``x-opencode-session`` header, so the header is sent whenever a session is
configured.
"""

from __future__ import annotations

import httpx

from fixjury.github import (
    JsonObject,
    optional_object,
    optional_string,
    require_array,
    require_object,
)


class LlmError(RuntimeError):
    """The provider refused the request or answered in an unusable shape."""


async def complete(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    api_key: str = "",
    session: str = "",
    temperature: float = 0.0,
) -> tuple[str, int, int]:
    """Return ``(text, tokens_in, tokens_out)`` for a chat completion."""

    if not model:
        raise LlmError("no model configured: set FIXJURY_LLM_MODEL")
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if session:
        headers["x-opencode-session"] = session

    try:
        response = await client.post(
            url,
            headers=headers,
            json={"model": model, "messages": messages, "temperature": temperature},
        )
    except httpx.HTTPError as exc:
        raise LlmError(f"POST {url} failed: {exc}") from exc
    if response.status_code >= httpx.codes.BAD_REQUEST:
        detail = response.text.strip().replace("\n", " ")[:300]
        raise LlmError(f"POST {url} failed: {response.status_code} {detail}")

    payload: object = response.json()
    if not isinstance(payload, dict):
        raise LlmError("provider returned a non-object payload")
    body: JsonObject = {str(key): value for key, value in payload.items()}
    choices = require_array(body, "choices", context="completion")
    if not choices:
        raise LlmError("provider returned no choices")
    message = require_object(choices[0], "message", context="completion.choices[0]")
    text = optional_string(message, "content") or ""
    usage = optional_object(body, "usage") or {}
    return text, _int(usage.get("prompt_tokens")), _int(usage.get("completion_tokens"))


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
