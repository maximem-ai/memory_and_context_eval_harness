"""
Concurrent executor — runs tasks with bounded concurrency and progress callbacks.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def execute_concurrent(
    items: List[T],
    concurrency: int,
    phase_name: str,
    task_fn: Callable[[T, int], Coroutine[Any, Any, Any]],
    on_progress: Optional[Callable[[int, int], None]] = None,
    rate_limit_ms: int = 0,
) -> List[Any]:
    """Execute tasks concurrently with a semaphore-based limit.

    Args:
        items: Items to process.
        concurrency: Max concurrent tasks.
        phase_name: For logging.
        task_fn: Async function taking (item, index) -> result.
        on_progress: Called with (completed_count, total_count) after each task.
        rate_limit_ms: Minimum delay between task starts (ms).

    Returns:
        List of results in order.
    """
    sem = asyncio.Semaphore(concurrency)
    results = [None] * len(items)
    completed = 0
    total = len(items)
    last_start = 0.0

    async def run_one(item: T, index: int) -> None:
        nonlocal completed, last_start
        async with sem:
            if rate_limit_ms > 0:
                now = time.monotonic()
                wait = (rate_limit_ms / 1000.0) - (now - last_start)
                if wait > 0:
                    await asyncio.sleep(wait)
                last_start = time.monotonic()

            try:
                results[index] = await task_fn(item, index)
            except Exception as e:
                logger.error("[%s] Task %d failed: %s", phase_name, index, e)
                results[index] = {"error": str(e)}

            completed += 1
            if on_progress:
                on_progress(completed, total)

    tasks = [run_one(item, i) for i, item in enumerate(items)]
    await asyncio.gather(*tasks)

    logger.info("[%s] Completed %d/%d tasks", phase_name, completed, total)
    return results
