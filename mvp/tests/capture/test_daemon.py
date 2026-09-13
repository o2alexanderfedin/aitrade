"""Tests for daemon.py's shutdown-race safety (CR-01, 01-REVIEW.md).

`request_shutdown()` (the SIGTERM/SIGINT path) was already proven race-free
by commit `b539e65`: cancel every producer task synchronously, THEN set
`shutdown_event`, with no `await` in between. `run_pipeline()`'s OTHER
shutdown trigger -- a producer finishing on its own (e.g.
`StartupLivenessError` on the very first connection attempt, or a genuinely
unexpected exception) -- reopened that exact race by calling a bare
`shutdown_event.set()` without cancelling the sibling first. This module
pins the fix: `_escalate_producer_completion()` must reuse
`request_shutdown()`, so the still-live sibling is cancelled BEFORE
`shutdown_event` is observed set by anything else, closing the drop window
where the sibling could slip one more `queue.put()` in before teardown.
"""

from __future__ import annotations

import asyncio

import pytest

from data.capture.daemon import run_pipeline


def test_escalate_producer_completion_cancels_sibling_before_shutdown_event_set() -> (
    None
):
    """One producer ("B") raises an unexpected exception while the sibling
    ("A") is still in flight (blocked forever in `asyncio.sleep`, standing
    in for a pending `ws.recv()`/`queue.put()`). The moment ANY observer
    sees `shutdown_event` set, the sibling must already have a cancellation
    request pending -- proving cancel-then-set ordering, mirroring
    `request_shutdown`'s already-correct SIGTERM-path guarantee."""

    async def scenario() -> None:
        shutdown_event = asyncio.Event()
        observed: dict[str, object] = {}

        async def producer_a_in_flight() -> None:
            # Stands in for a producer blocked on `ws.recv()`/`queue.put()`
            # with an item "in flight" -- must be cancelled, not left to
            # run past the consumer's final snapshot.
            await asyncio.sleep(100)

        async def producer_b_unexpected_failure() -> None:
            await asyncio.sleep(0.02)
            raise RuntimeError("simulated unexpected producer failure")

        task_a = asyncio.create_task(producer_a_in_flight(), name="producer-A")
        task_b = asyncio.create_task(producer_b_unexpected_failure(), name="producer-B")
        producer_tasks = [task_a, task_b]

        async def fake_consumer() -> None:
            # Mirrors rotation.consume()'s contract: only returns once
            # shutdown_event is set.
            await shutdown_event.wait()

        consumer_task = asyncio.create_task(fake_consumer())

        async def observer() -> None:
            await shutdown_event.wait()
            # Captured at the instant this observer sees the event set --
            # if request_shutdown's cancel-then-set ordering holds, task_a's
            # cancellation must already have been REQUESTED here (delivery
            # can still be pending, since cancellation completes async).
            observed["cancelling"] = task_a.cancelling()

        observer_task = asyncio.create_task(observer())

        with pytest.raises(RuntimeError, match="simulated unexpected producer failure"):
            await run_pipeline(producer_tasks, consumer_task, shutdown_event)

        await observer_task

        assert observed["cancelling"] > 0, (
            "sibling producer task_a was not cancelled before shutdown_event "
            "was observed set -- the drop race CR-01 fixes reopened"
        )
        # By the time run_pipeline has fully returned, cancellation has been
        # fully delivered and the sibling is done.
        assert task_a.cancelled()

    asyncio.run(scenario())


def test_escalate_producer_completion_on_normal_return_also_cancels_sibling() -> None:
    """A producer that returns normally (not expected in production, but
    `run_pipeline`'s docstring calls this out explicitly) must ALSO trigger
    cancel-then-set for its sibling -- the fix is not exception-specific."""

    async def scenario() -> None:
        shutdown_event = asyncio.Event()

        async def producer_a_in_flight() -> None:
            await asyncio.sleep(100)

        async def producer_b_returns_normally() -> None:
            await asyncio.sleep(0.02)
            return

        task_a = asyncio.create_task(producer_a_in_flight(), name="producer-A")
        task_b = asyncio.create_task(producer_b_returns_normally(), name="producer-B")
        producer_tasks = [task_a, task_b]

        async def fake_consumer() -> None:
            await shutdown_event.wait()

        consumer_task = asyncio.create_task(fake_consumer())

        await run_pipeline(producer_tasks, consumer_task, shutdown_event)

        assert shutdown_event.is_set()
        assert task_a.cancelled()

    asyncio.run(scenario())
