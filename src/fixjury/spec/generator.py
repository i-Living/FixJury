"""RepairSpec draft generation: issue plus fix file list -> ``task.yaml`` (PLAN §8, 1.4).

The model is allowed to phrase acceptance criteria, but not to invent scope:
every ``allowed_paths`` entry and every path in ``checks`` must be a file (or a
directory containing one) that the upstream fix actually touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml
from pydantic import ValidationError

from fixjury.github import get_array, get_object, repo_api_url, require_string
from fixjury.llm import complete
from fixjury.tasks.schema import CheckTool, TaskDocument, parse_check

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "spec_generate.md"


class DraftError(RuntimeError):
    """The model's answer cannot become a task draft."""


@dataclass(frozen=True)
class IssueContext:
    number: int
    url: str
    title: str
    body: str


@dataclass(frozen=True)
class DraftResult:
    document: TaskDocument
    tokens_in: int
    tokens_out: int
    attempts: int


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


async def load_issue(
    *, client: httpx.AsyncClient, api_url: str, repo_url: str, issue_number: int, token: str = ""
) -> IssueContext:
    api = repo_api_url(api_url, repo_url)
    issue = await get_object(client, f"{api}/issues/{issue_number}", token=token)
    if "pull_request" in issue:
        raise DraftError(f"issue #{issue_number} is a pull request")
    return IssueContext(
        number=issue_number,
        url=require_string(issue, "html_url", context="issue"),
        title=require_string(issue, "title", context="issue"),
        body=require_string(issue, "body", context="issue") if issue.get("body") else "",
    )


async def fix_candidate_paths(
    *, client: httpx.AsyncClient, api_url: str, repo_url: str, pull_number: int, token: str = ""
) -> tuple[str, ...]:
    """Files the upstream fix touched; the only legal scope for a draft."""

    api = repo_api_url(api_url, repo_url)
    files = await get_array(
        client, f"{api}/pulls/{pull_number}/files", token=token, params={"per_page": "100"}
    )
    return tuple(require_string(item, "filename", context="pull request file") for item in files)


def build_messages(
    *,
    prompt: str,
    repo: str,
    issue: IssueContext,
    task_id: str,
    candidate_paths: tuple[str, ...],
) -> list[dict[str, str]]:
    body = issue.body.strip() or "(the issue body is empty)"
    user = (
        f"task id: {task_id}\n"
        f"repository: {repo}\n"
        f"issue number: {issue.number}\n"
        f"issue url: {issue.url}\n"
        f"issue title: {issue.title}\n"
        f"candidate_paths:\n"
        + "".join(f"  - {path}\n" for path in candidate_paths)
        + f"\nissue body:\n{body}\n"
    )
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user},
    ]


def extract_yaml(text: str) -> str:
    """Strip a fenced code block if the model wrapped its answer in one."""

    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


def _path_is_covered(path: str, candidates: tuple[str, ...]) -> bool:
    base = path.rstrip("/")
    return path in candidates or any(item.startswith(base + "/") for item in candidates)


def draft_from_text(
    text: str,
    *,
    task_id: str,
    repo: str,
    issue: IssueContext,
    candidate_paths: tuple[str, ...],
) -> TaskDocument:
    """Parse the model's answer into a draft, refusing invented paths."""

    raw = yaml.safe_load(extract_yaml(text))
    if not isinstance(raw, dict):
        raise DraftError("answer is not a YAML mapping")
    if "error" in raw:
        raise DraftError(f"model declined the issue: {raw['error']}")
    payload = {str(key): value for key, value in raw.items()}
    payload.update(
        {
            "id": task_id,
            "repo": repo,
            "issue": {"number": issue.number, "url": issue.url},
            "heldout_ref": f"benchmarks/heldout/{task_id}",
        }
    )
    try:
        document = TaskDocument.model_validate(payload)
    except ValidationError as exc:
        parts = [
            f"{'.'.join(str(i) for i in error['loc'])}: {error['msg']}" for error in exc.errors()
        ]
        raise DraftError("; ".join(parts)) from exc

    if not document.checks:
        raise DraftError("checks are empty")
    for raw_check in document.checks:
        command = parse_check(raw_check)
        if command.tool is not CheckTool.pytest:
            raise DraftError(f"only pytest checks are allowed, got {raw_check!r}")
        for argument in command.args:
            if not _path_is_covered(argument, candidate_paths):
                raise DraftError(
                    f"check path {argument!r} is not part of the files the upstream fix touched"
                )
    for path in document.allowed_paths:
        if not _path_is_covered(path, candidate_paths):
            raise DraftError(
                f"allowed path {path!r} is not part of the files the upstream fix touched"
            )
    return document


async def generate_draft(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    api_key: str,
    session: str,
    repo: str,
    issue: IssueContext,
    task_id: str,
    candidate_paths: tuple[str, ...],
) -> DraftResult:
    """Generate, validate, and on failure retry once with the error attached."""

    messages = build_messages(
        prompt=load_prompt(),
        repo=repo,
        issue=issue,
        task_id=task_id,
        candidate_paths=candidate_paths,
    )
    tokens_in = 0
    tokens_out = 0
    attempt = 0
    last_error: DraftError | None = None
    for attempt in range(1, 3):
        text, prompt_tokens, completion_tokens = await complete(
            client=client,
            base_url=base_url,
            model=model,
            messages=messages,
            api_key=api_key,
            session=session,
        )
        tokens_in += prompt_tokens
        tokens_out += completion_tokens
        try:
            document = draft_from_text(
                text,
                task_id=task_id,
                repo=repo,
                issue=issue,
                candidate_paths=candidate_paths,
            )
        except DraftError as exc:
            last_error = exc
            messages = [
                *messages,
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        f"The draft is invalid: {exc}. Return the corrected YAML document only."
                    ),
                },
            ]
            continue
        return DraftResult(
            document=document, tokens_in=tokens_in, tokens_out=tokens_out, attempts=attempt
        )
    raise DraftError(f"no valid draft after {attempt} attempts: {last_error}")
