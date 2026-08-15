"""
Report Phase — aggregate evaluation results into a BenchmarkResult.

Computes accuracy, latency stats, retrieval metrics, and per-question-type
breakdowns.
"""

import logging
import math
from typing import Dict, List

from runner.types import (
    Benchmark,
    BenchmarkResult,
    BenchmarkResultSummary,
    EvaluationResult,
    LatencyStats,
    QuestionTypeStats,
    RetrievalAggregates,
    RetrievalMetrics,
    RunCheckpoint,
)
from runner.checkpoint import CheckpointManager

logger = logging.getLogger(__name__)


def generate_report(
    benchmark: Benchmark,
    checkpoint: RunCheckpoint,
    checkpoint_mgr: CheckpointManager,
) -> BenchmarkResult:
    """Generate a BenchmarkResult from completed evaluations."""
    evaluations: List[EvaluationResult] = []
    search_durations: List[float] = []
    answer_durations: List[float] = []
    evaluate_durations: List[float] = []
    total_durations: List[float] = []
    retrieval_metrics_list: List[RetrievalMetrics] = []

    for qid, qcp in checkpoint.questions.items():
        phases = qcp.phases or {}

        # Extract phase data
        search_p = phases.get("search", {})
        answer_p = phases.get("answer", {})
        eval_p = phases.get("evaluate", {})

        s_dur = _get_duration(search_p)
        a_dur = _get_duration(answer_p)
        e_dur = _get_duration(eval_p)
        t_dur = s_dur + a_dur + e_dur

        score = _get_field(eval_p, "score", 0.0)
        label = _get_field(eval_p, "label", "incorrect")
        hypothesis = _get_field(answer_p, "hypothesis", "")
        explanation = _get_field(eval_p, "explanation", "")

        # Load search results
        search_data = checkpoint_mgr.load_search_results(checkpoint.run_id, qid)
        search_results = search_data.get("results", []) if search_data else []

        # Retrieval metrics
        rm_raw = _get_field(eval_p, "retrieval_metrics", None)
        rm = None
        if rm_raw:
            if isinstance(rm_raw, RetrievalMetrics):
                rm = rm_raw
            elif isinstance(rm_raw, dict):
                rm = RetrievalMetrics(**rm_raw)
            retrieval_metrics_list.append(rm)

        evaluations.append(EvaluationResult(
            question_id=qid,
            question_type=qcp.question_type,
            question=qcp.question,
            score=score,
            label=label,
            explanation=explanation,
            hypothesis=hypothesis,
            ground_truth=qcp.ground_truth,
            search_results=search_results,
            search_duration_ms=s_dur,
            answer_duration_ms=a_dur,
            total_duration_ms=t_dur,
            retrieval_metrics=rm,
        ))

        if s_dur > 0:
            search_durations.append(s_dur)
        if a_dur > 0:
            answer_durations.append(a_dur)
        if e_dur > 0:
            evaluate_durations.append(e_dur)
        if t_dur > 0:
            total_durations.append(t_dur)

    # Summary
    total_q = len(evaluations)
    correct = sum(1 for e in evaluations if e.label == "correct")
    accuracy = (correct / total_q * 100) if total_q > 0 else 0.0

    # Latency
    latency = {
        "search": _compute_latency_stats(search_durations),
        "answer": _compute_latency_stats(answer_durations),
        "evaluate": _compute_latency_stats(evaluate_durations),
        "total": _compute_latency_stats(total_durations),
    }

    # Retrieval aggregates
    retrieval = _compute_retrieval_aggregates(retrieval_metrics_list) if retrieval_metrics_list else None

    # Per question type
    by_type: Dict[str, QuestionTypeStats] = {}
    type_evals: Dict[str, List[EvaluationResult]] = {}
    for e in evaluations:
        type_evals.setdefault(e.question_type, []).append(e)

    for qtype, evals in type_evals.items():
        t_total = len(evals)
        t_correct = sum(1 for e in evals if e.label == "correct")
        t_accuracy = (t_correct / t_total * 100) if t_total > 0 else 0.0

        t_search_dur = [e.search_duration_ms for e in evals if e.search_duration_ms > 0]
        t_answer_dur = [e.answer_duration_ms for e in evals if e.answer_duration_ms > 0]
        t_total_dur = [e.total_duration_ms for e in evals if e.total_duration_ms > 0]

        t_ret = [e.retrieval_metrics for e in evals if e.retrieval_metrics]
        t_retrieval = _compute_retrieval_aggregates(t_ret) if t_ret else None

        by_type[qtype] = QuestionTypeStats(
            total=t_total,
            correct=t_correct,
            accuracy=t_accuracy,
            latency={
                "search": _compute_latency_stats(t_search_dur),
                "answer": _compute_latency_stats(t_answer_dur),
                "total": _compute_latency_stats(t_total_dur),
            },
            retrieval=t_retrieval,
        )

    result = BenchmarkResult(
        provider=checkpoint.provider,
        benchmark=checkpoint.benchmark,
        run_id=checkpoint.run_id,
        global_container_tag=checkpoint.global_container_tag,
        judge=checkpoint.judge,
        answering_model=checkpoint.answering_model,
        timestamp=checkpoint.updated_at,
        summary=BenchmarkResultSummary(
            total_questions=total_q,
            correct_count=correct,
            accuracy=accuracy,
        ),
        latency=latency,
        retrieval=retrieval,
        by_question_type=by_type,
        question_type_registry=benchmark.get_question_types(),
        evaluations=evaluations,
    )

    logger.info(
        "[report] %s/%s: %d/%d correct (%.1f%%), retrieval: %s",
        checkpoint.provider, checkpoint.benchmark,
        correct, total_q, accuracy,
        f"Hit@K={retrieval.hit_at_k:.2f} MRR={retrieval.mrr:.2f}" if retrieval else "N/A",
    )

    return result


# ── Helpers ────────────────────────────────────────────────────────


def _get_field(phase_data, field: str, default=None):
    if isinstance(phase_data, dict):
        return phase_data.get(field, default)
    return getattr(phase_data, field, default)


def _get_duration(phase_data) -> float:
    return _get_field(phase_data, "duration_ms", 0.0) or 0.0


def _compute_latency_stats(values: List[float]) -> LatencyStats:
    if not values:
        return LatencyStats()

    sorted_v = sorted(values)
    n = len(sorted_v)
    mean = sum(sorted_v) / n
    median = sorted_v[n // 2] if n % 2 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    p95 = sorted_v[int(n * 0.95)] if n > 1 else sorted_v[0]
    p99 = sorted_v[int(n * 0.99)] if n > 1 else sorted_v[0]
    variance = sum((v - mean) ** 2 for v in sorted_v) / n if n > 1 else 0
    std_dev = math.sqrt(variance)

    return LatencyStats(
        min=sorted_v[0],
        max=sorted_v[-1],
        mean=round(mean, 1),
        median=round(median, 1),
        p95=round(p95, 1),
        p99=round(p99, 1),
        std_dev=round(std_dev, 1),
        count=n,
    )


def _compute_retrieval_aggregates(metrics: List[RetrievalMetrics]) -> RetrievalAggregates:
    if not metrics:
        return RetrievalAggregates()

    n = len(metrics)
    return RetrievalAggregates(
        hit_at_k=round(sum(m.hit_at_k for m in metrics) / n, 4),
        precision_at_k=round(sum(m.precision_at_k for m in metrics) / n, 4),
        recall_at_k=round(sum(m.recall_at_k for m in metrics) / n, 4),
        f1_at_k=round(sum(m.f1_at_k for m in metrics) / n, 4),
        mrr=round(sum(m.mrr for m in metrics) / n, 4),
        ndcg=round(sum(m.ndcg for m in metrics) / n, 4),
        k=metrics[0].k if metrics else 10,
    )
