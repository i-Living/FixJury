"""Minimal GitHub REST client for the corpus tooling (PLAN §8).

Responses are parsed into plain ``dict[str, object]`` values and read through
typed accessors: an unexpected payload must fail with a readable message, not a
``KeyError`` three frames down.
"""

from __future__ import annotations

import re

import httpx

USER_AGENT = "fixjury"
API_VERSION = "2022-11-28"

type JsonObject = dict[str, object]

_REPO_URL = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+)$"
)


class GithubError(RuntimeError):
    """GitHub answered with an error or with a payload we cannot use."""


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    match = _REPO_URL.match(repo_url.rstrip("/"))
    if match is None:
        raise GithubError(f"not a GitHub repository URL: {repo_url!r}")
    return match.group("owner"), match.group("name")


def repo_api_url(api_url: str, repo_url: str) -> str:
    owner, name = parse_repo_url(repo_url)
    return f"{api_url.rstrip('/')}/repos/{owner}/{name}"


def _headers(token: str) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def get_object(
    client: httpx.AsyncClient, url: str, *, token: str = "", params: dict[str, str] | None = None
) -> JsonObject:
    """GET a JSON object, raising :class:`GithubError` on failure."""

    payload = await _get(client, url, token=token, params=params)
    if not isinstance(payload, dict):
        raise GithubError(f"GET {url} returned {type(payload).__name__}, expected an object")
    return {str(key): value for key, value in payload.items()}


async def get_array(
    client: httpx.AsyncClient, url: str, *, token: str = "", params: dict[str, str] | None = None
) -> list[JsonObject]:
    """GET a JSON array of objects."""

    payload = await _get(client, url, token=token, params=params)
    if not isinstance(payload, list):
        raise GithubError(f"GET {url} returned {type(payload).__name__}, expected an array")
    items: list[JsonObject] = []
    for item in payload:
        if not isinstance(item, dict):
            raise GithubError(f"GET {url} returned an array element that is not an object")
        items.append({str(key): value for key, value in item.items()})
    return items


async def _get(
    client: httpx.AsyncClient, url: str, *, token: str, params: dict[str, str] | None
) -> object:
    try:
        response = await client.get(url, headers=_headers(token), params=params)
    except httpx.HTTPError as exc:
        raise GithubError(f"GET {url} failed: {exc}") from exc
    if response.status_code >= httpx.codes.BAD_REQUEST:
        detail = response.text.strip().replace("\n", " ")[:200]
        raise GithubError(f"GET {url} failed: {response.status_code} {detail}")
    try:
        return response.json()
    except ValueError as exc:
        raise GithubError(f"GET {url} returned invalid JSON") from exc


def require_string(payload: JsonObject, key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GithubError(f"{context}: field {key!r} is missing or not a string")
    return value


def optional_string(payload: JsonObject, key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def optional_object(payload: JsonObject, key: str) -> JsonObject | None:
    value = payload.get(key)
    if not isinstance(value, dict):
        return None
    return {str(item_key): item_value for item_key, item_value in value.items()}


def require_int(payload: JsonObject, key: str, *, context: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise GithubError(f"{context}: field {key!r} is missing or not an integer")
    return value


def require_object(payload: JsonObject, key: str, *, context: str) -> JsonObject:
    value = optional_object(payload, key)
    if value is None:
        raise GithubError(f"{context}: field {key!r} is missing or not an object")
    return value


def require_array(payload: JsonObject, key: str, *, context: str) -> list[JsonObject]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise GithubError(f"{context}: field {key!r} is missing or not an array")
    items: list[JsonObject] = []
    for item in value:
        if not isinstance(item, dict):
            raise GithubError(f"{context}: field {key!r} holds a non-object element")
        items.append({str(item_key): item_value for item_key, item_value in item.items()})
    return items
