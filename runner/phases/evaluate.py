"""
Evaluate Phase — score hypotheses against ground truth + compute retrieval metrics.

Runs judge_single() and judge_retrieval_quality() in parallel for ALL benchmarks.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from runner.types import (
    Benchmark,
    Provider,
    RetrievalMetrics,
    RunCheckpoint,
    resolve_concurrency,
)
from runner.checkpoint import CheckpointManager
from runner.concurrent import execute_concurrent

logger = logging.getLogger(__name__)


async def run_evaluate_phase(
    benchmark: Benchmark,
    checkpoint: RunCheckpoint,
    checkpoint_mgr: CheckpointManager,
    judge_model: str,
    provider: Optional[Provider] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Run the evaluate phase for all questions with completed answers."""
    pending = [
        qid for qid, qcp in checkpoint.questions.items()
        if (
            checkpoint_mgr.get_phase_status(checkpoint, qid, "answer") == "completed"
            and checkpoint_mgr.get_phase_status(checkpoint, qid, "evaluate") != "completed"
        )
    ]

    if not pending:
        logger.info("[evaluate] All questions already evaluated")
        return

    concurrency = resolve_concurrency(
        "evaluate",
        checkpoint.concurrency,
        provider.concurrency if provider else None,
    )
    logger.info("[evaluate] Evaluating %d questions (judge=%s, concurrency=%d)", len(pending), judge_model, concurrency)

    async def evaluate_one(question_id: str, index: int) -> Dict[str, Any]:
        qcp = checkpoint.questions[question_id]
        t0 = time.monotonic()

        checkpoint_mgr.update_evaluate_phase(
            checkpoint, question_id, status="in_progress",
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        try:
            # Get hypothesis from answer phase
            answer_phase = qcp.phases.get("answer", {})
            hypothesis = (
                answer_phase.get("hypothesis") if isinstance(answer_phase, dict)
                else getattr(answer_phase, "hypothesis", "")
            ) or ""

            # Load search results for retrieval eval
            search_data = checkpoint_mgr.load_search_results(checkpoint.run_id, question_id)
            search_results = search_data.get("results", []) if search_data else []

            # Run judge + retrieval eval in parallel
            import asyncio
            judge_task = asyncio.create_task(
                _run_judge(
                    qcp.question, qcp.ground_truth, hypothesis,
                    qcp.question_type, judge_model,
                )
            )
            retrieval_task = asyncio.create_task(
                _run_retrieval_eval(
                    qcp.question, qcp.ground_truth, search_results,
                    qcp.question_type, judge_model,
                )
            )

            judge_result, retrieval_metrics = await asyncio.gather(judge_task, retrieval_task)
            duration_ms = round((time.monotonic() - t0) * 1000, 1)

            score = judge_result.get("score", 0)
            label = "correct" if score >= 0.5 else "incorrect"

            checkpoint_mgr.update_evaluate_phase(
                checkpoint, question_id,
                status="completed",
                score=score,
                label=label,
                explanation=judge_result.get("explanation", ""),
                retrieval_metrics=retrieval_metrics,
                duration_ms=duration_ms,
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            await checkpoint_mgr.save(checkpoint)

            if on_progress:
                on_progress({
                    "type": "evaluate_complete",
                    "question_id": question_id,
                    "score": score,
                    "label": label,
                    "duration_ms": duration_ms,
                })

            return {
                "question_id": question_id,
                "score": score,
                "label": label,
                "duration_ms": duration_ms,
            }

        except Exception as e:
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            checkpoint_mgr.update_evaluate_phase(
                checkpoint, question_id,
                status="failed",
                error=str(e),
                duration_ms=duration_ms,
            )
            await checkpoint_mgr.save(checkpoint)
            logger.error("[evaluate] Question %s failed: %s", question_id, e)
            return {"question_id": question_id, "error": str(e)}

    await execute_concurrent(pending, concurrency, "evaluate", evaluate_one)


async def _run_judge(
    question: str,
    ground_truth: str,
    hypothesis: str,
    question_type: str,
    model: str,
) -> Dict[str, Any]:
    """Run the answer judge (reuses existing scorers.llm_judge)."""
    try:
        from scorers.llm_judge import judge_single
        result = await judge_single(
            question=question,
            prediction=hypothesis,
            gold=ground_truth,
            model=model,
            question_type=question_type,
        )
        return result
    except Exception as e:
        logger.error("[evaluate] Judge failed: %s", e)
        return {"score": 0, "explanation": f"Judge error: {e}"}


async def _run_retrieval_eval(
    question: str,
    ground_truth: str,
    search_results: list,
    question_type: str,
    model: str,
) -> Optional[RetrievalMetrics]:
    """Run retrieval quality evaluation (reuses existing scorers.llm_judge)."""
    if not search_results:
        return None

    try:
        from scorers.llm_judge import judge_retrieval_quality

        result = await judge_retrieval_quality(
            question=question,
            gold=ground_truth,
            item_objects=search_results,
            model=model,
            question_type=question_type,
        )

        if result and "hit_at_k" in result:
            return RetrievalMetrics(
                hit_at_k=result.get("hit_at_k", 0.0),
                precision_at_k=result.get("precision_at_k", 0.0),
                recall_at_k=result.get("recall_at_k", 0.0),
                f1_at_k=result.get("f1_at_k", 0.0),
                mrr=result.get("mrr", 0.0),
                ndcg=result.get("ndcg", 0.0),
                k=result.get("k", 10),
                relevant_retrieved=result.get("relevant_retrieved", 0),
                total_relevant=result.get("total_relevant", 0),
            )
        return None
    except Exception as e:
        logger.error("[evaluate] Retrieval eval failed: %s", e)
        return None
