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


async def run_search_phase(
    provider: Provider,
    benchmark: Benchmark,
    checkpoint: RunCheckpoint,
    checkpoint_mgr: CheckpointManager,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Run the search phase for all pending questions."""
    # Find questions needing search
    pending = [
        qid for qid, qcp in checkpoint.questions.items()
        if checkpoint_mgr.get_phase_status(checkpoint, qid, "search") != "completed"
    ]

    if not pending:
        logger.info("[search] All questions already searched")
        return

    logger.info("[search] Searching %d questions (concurrency=%d)", len(pending), _get_concurrency(checkpoint, provider))

    concurrency = _get_concurrency(checkpoint, provider)

    async def search_one(question_id: str, index: int) -> Dict[str, Any]:
        qcp = checkpoint.questions[question_id]
        t0 = time.monotonic()

        # Update status
        checkpoint_mgr.update_search_phase(
            checkpoint, question_id, status="in_progress", started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
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

            # Save results to disk
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

            # Update checkpoint
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

    await execute_concurrent(
        pending, concurrency, "search", search_one,
        on_progress=lambda done, total: None,  # progress via per-question callback
    )


def _get_concurrency(checkpoint: RunCheckpoint, provider: Provider) -> int:
    return resolve_concurrency("search", checkpoint.concurrency, provider.concurrency)
