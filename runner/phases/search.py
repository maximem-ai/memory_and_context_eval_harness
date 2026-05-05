"""
Search Phase — retrieve context for each question from the provider.

Results are saved to disk per question, enabling re-running answer+evaluate
without re-searching.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from runner.types import (
    Benchmark,
    Provider,
    RunCheckpoint,
    SearchOptions,
    resolve_concurrency,
)
from runner.checkpoint import CheckpointManager
from runner.concurrent import execute_concurrent

logger = logging.getLogger(__name__)


async def search_one(
    question_id: str,
    provider: Provider,
    checkpoint: RunCheckpoint,
    checkpoint_mgr: CheckpointManager,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Search a single question and persist results. Returns a status dict."""
    qcp = checkpoint.questions[question_id]
    t0 = time.monotonic()

    checkpoint_mgr.update_search_phase(
        checkpoint, question_id, status="in_progress",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    try:
        results = await provider.search(
            qcp.question,
            SearchOptions(
                container_tag=qcp.container_tag,
                limit=10,
                threshold=0.3,
            ),
        )
        duration_ms = round((time.monotonic() - t0) * 1000, 1)

        result_data = {
            "question_id": question_id,
            "question": qcp.question,
            "question_type": qcp.question_type,
            "ground_truth": qcp.ground_truth,
            "container_tag": qcp.container_tag,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_ms": duration_ms,
            "results": results,
        }
        result_file = checkpoint_mgr.save_search_results(
            checkpoint.run_id, question_id, result_data,
        )

        checkpoint_mgr.update_search_phase(
            checkpoint, question_id,
            status="completed",
            result_file=result_file,
            result_count=len(results),
            duration_ms=duration_ms,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        await checkpoint_mgr.save(checkpoint)

        if on_progress:
            on_progress({
                "type": "search_complete",
                "question_id": question_id,
                "result_count": len(results),
                "duration_ms": duration_ms,
            })

        return {"question_id": question_id, "result_count": len(results), "duration_ms": duration_ms}

    except Exception as e:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        checkpoint_mgr.update_search_phase(
            checkpoint, question_id,
            status="failed",
            error=str(e),
            duration_ms=duration_ms,
        )
        await checkpoint_mgr.save(checkpoint)
        logger.error("[search] Question %s failed: %s", question_id, e)
        return {"question_id": question_id, "error": str(e)}


async def run_search_phase(
    provider: Provider,
    benchmark: Benchmark,
    checkpoint: RunCheckpoint,
    checkpoint_mgr: CheckpointManager,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Run the search phase for all pending questions (serial-phase mode)."""
    pending = [
        qid for qid, qcp in checkpoint.questions.items()
        if checkpoint_mgr.get_phase_status(checkpoint, qid, "search") != "completed"
    ]

    if not pending:
        logger.info("[search] All questions already searched")
        return

    concurrency = _get_concurrency(checkpoint, provider)
    logger.info("[search] Searching %d questions (concurrency=%d)", len(pending), concurrency)

    async def _wrap(question_id: str, index: int) -> Dict[str, Any]:
        return await search_one(question_id, provider, checkpoint, checkpoint_mgr, on_progress)

    await execute_concurrent(
        pending, concurrency, "search", _wrap,
        on_progress=lambda done, total: None,
    )

    empty = sum(
        1 for qid, qcp in checkpoint.questions.items()
        if (qcp.phases or {}).get("search") and (
            getattr(qcp.phases["search"], "result_count", None) == 0
            or (isinstance(qcp.phases["search"], dict) and qcp.phases["search"].get("result_count") == 0)
        )
    )
    total = len(checkpoint.questions)
    if total and empty / total >= 0.2:
        logger.warning(
            "[search] %d/%d questions returned 0 results (%.0f%%) — check container_tag mapping",
            empty, total, 100 * empty / total,
        )
    elif empty:
        logger.info("[search] %d/%d questions returned 0 results", empty, total)


def _get_concurrency(checkpoint: RunCheckpoint, provider: Provider) -> int:
    return resolve_concurrency("search", checkpoint.concurrency, provider.concurrency)
