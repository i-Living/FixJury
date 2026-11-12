from __future__ import annotations

import asyncio

from fixjury import queue


class FakeRedis:
    """The list commands the queue uses, in memory."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    async def rpush(self, key: str, *values: str) -> int:
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    async def lrem(self, key: str, count: int, value: str) -> int:
        items = self.lists.get(key, [])
        if value not in items:
            return 0
        items.remove(value)
        return 1

    async def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return list(self.lists.get(key, []))

    async def blmove(
        self,
        source: str,
        destination: str,
        wait_s: float,
        src: str = "LEFT",
        dest: str = "RIGHT",
    ) -> str | None:
        items = self.lists.get(source, [])
        if not items:
            return None
        value = items.pop(0)
        self.lists.setdefault(destination, []).append(value)
        return value


def test_the_first_attempt_retries_once() -> None:
    assert queue.next_attempt(1) == 2


def test_the_attempt_budget_is_spent() -> None:
    assert queue.next_attempt(queue.MAX_ATTEMPTS) is None


def test_claim_moves_the_run_into_processing() -> None:
    client = FakeRedis()

    async def scenario() -> tuple[str | None, int, int]:
        await client.rpush(queue.QUEUED_KEY, "a")
        claimed = await queue.claim(client)
        counts = await queue.depth(client)
        return claimed, counts["queued"], counts["processing"]

    claimed, queued, processing = asyncio.run(scenario())

    assert claimed == "a"
    assert (queued, processing) == (0, 1)


def test_claiming_an_empty_queue_returns_none() -> None:
    assert asyncio.run(queue.claim(FakeRedis())) is None


def test_completing_a_run_drops_it_from_processing() -> None:
    client = FakeRedis()

    async def scenario() -> dict[str, int]:
        await client.rpush(queue.PROCESSING_KEY, "a")
        await queue.complete(client, "a")
        return await queue.depth(client)

    assert asyncio.run(scenario()) == {"queued": 0, "processing": 0}


def test_resume_returns_orphans_to_the_queue() -> None:
    client = FakeRedis()

    async def scenario() -> tuple[int, dict[str, int]]:
        await client.rpush(queue.PROCESSING_KEY, "a")
        await client.rpush(queue.PROCESSING_KEY, "b")
        recovered = await queue.resume(client)
        return recovered, await queue.depth(client)

    recovered, counts = asyncio.run(scenario())

    assert recovered == 2
    assert counts == {"queued": 2, "processing": 0}


def test_resume_on_an_empty_queue_does_nothing() -> None:
    assert asyncio.run(queue.resume(FakeRedis())) == 0


def test_claim_is_first_in_first_out() -> None:
    client = FakeRedis()

    async def scenario() -> list[str]:
        for run_id in ("a", "b", "c"):
            await client.rpush(queue.QUEUED_KEY, run_id)
        first = await queue.claim(client)
        second = await queue.claim(client)
        return [item for item in (first, second) if item is not None]

    assert asyncio.run(scenario()) == ["a", "b"]


def test_a_bad_run_id_is_reported() -> None:
    import pytest

    with pytest.raises(queue.QueueError, match="not a run id"):
        queue.parse_run_id("not-a-uuid")
