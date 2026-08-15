"""
Run checkpoint manager — per-question, per-phase tracking with atomic writes.

Stores checkpoints at: data/runs/{run_id}/checkpoint.json
Search results at:     data/runs/{run_id}/results/{question_id}.json
Reports at:            data/runs/{run_id}/report.json
"""

import asyncio
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from runner.types import (
    AnswerPhaseCheckpoint,
    ConcurrencyConfig,
    EvaluatePhaseCheckpoint,
    PhaseId,
    PhaseStatus,
    QuestionCheckpoint,
    RetrievalMetrics,
    RunCheckpoint,
    SamplingConfig,
    SearchPhaseCheckpoint,
    get_phases_from_phase,
)

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
RUNS_DIR = os.path.join(DATA_DIR, "runs")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


class CheckpointManager:
    """Manages run checkpoints with atomic file writes."""

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, run_id: str) -> asyncio.Lock:
        if run_id not in self._locks:
            self._locks[run_id] = asyncio.Lock()
        return self._locks[run_id]

    def _run_dir(self, run_id: str) -> str:
        return os.path.join(RUNS_DIR, run_id)

    def _checkpoint_path(self, run_id: str) -> str:
        return os.path.join(self._run_dir(run_id), "checkpoint.json")

    def _results_dir(self, run_id: str) -> str:
        return os.path.join(self._run_dir(run_id), "results")

    def _report_path(self, run_id: str) -> str:
        return os.path.join(self._run_dir(run_id), "report.json")

    # ── Atomic write ──────────────────────────────────────────────────

    def _atomic_write(self, path: str, data: dict) -> None:
        """Write JSON atomically via tmp + rename."""
        _ensure_dir(os.path.dirname(path))
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, path)

    # ── CRUD ──────────────────────────────────────────────────────────

    def load(self, run_id: str) -> Optional[RunCheckpoint]:
        """Load a checkpoint from disk, or None if not found."""
        path = self._checkpoint_path(run_id)
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            data = json.load(f)
        return self._from_dict(data)

    def create(
        self,
        run_id: str,
        provider: str,
        benchmark: str,
        global_container_tag: str,
        judge: str = "",
        answering_model: str = "",
        isolation_mode: str = "global",
        sampling: Optional[SamplingConfig] = None,
        concurrency: Optional[ConcurrencyConfig] = None,
        target_question_ids: Optional[List[str]] = None,
        provider_config: Optional[Dict[str, Any]] = None,
    ) -> RunCheckpoint:
        """Create a new run checkpoint."""
        now = _now_iso()
        cp = RunCheckpoint(
            run_id=run_id,
            global_container_tag=global_container_tag,
            isolation_mode=isolation_mode,
            status="initializing",
            provider=provider,
            benchmark=benchmark,
            judge=judge,
            answering_model=answering_model,
            created_at=now,
            updated_at=now,
            sampling=sampling,
            concurrency=concurrency,
            target_question_ids=target_question_ids,
            provider_config=provider_config,
        )
        self._save_sync(cp)
        return cp

    async def save(self, checkpoint: RunCheckpoint) -> None:
        """Save checkpoint atomically (async with lock)."""
        lock = self._get_lock(checkpoint.run_id)
        async with lock:
            checkpoint.updated_at = _now_iso()
            self._atomic_write(
                self._checkpoint_path(checkpoint.run_id),
                self._to_dict(checkpoint),
            )

    def _save_sync(self, checkpoint: RunCheckpoint) -> None:
        """Save checkpoint synchronously (for create)."""
        checkpoint.updated_at = _now_iso()
        self._atomic_write(
            self._checkpoint_path(checkpoint.run_id),
            self._to_dict(checkpoint),
        )

    # ── Question management ───────────────────────────────────────────

    def init_question(
        self,
        checkpoint: RunCheckpoint,
        question_id: str,
        container_tag: str,
        question: str,
        ground_truth: str,
        question_type: str,
        question_date: Optional[str] = None,
    ) -> None:
        """Add a question to the checkpoint if not already present."""
        if question_id not in checkpoint.questions:
            checkpoint.questions[question_id] = QuestionCheckpoint(
                question_id=question_id,
                container_tag=container_tag,
                question=question,
                ground_truth=ground_truth,
                question_type=question_type,
                question_date=question_date,
            )

    # ── Phase updates ─────────────────────────────────────────────────

    def get_phase_status(
        self, checkpoint: RunCheckpoint, question_id: str, phase: PhaseId
    ) -> PhaseStatus:
        """Get the status of a specific phase for a question."""
        qcp = checkpoint.questions.get(question_id)
        if not qcp or not qcp.phases:
            return "pending"
        phase_cp = qcp.phases.get(phase)
        if phase_cp is None:
            return "pending"
        if isinstance(phase_cp, dict):
            return phase_cp.get("status", "pending")
        return phase_cp.status

    def update_search_phase(
        self,
        checkpoint: RunCheckpoint,
        question_id: str,
        **updates,
    ) -> None:
        """Update the search phase for a question."""
        qcp = checkpoint.questions.get(question_id)
        if not qcp:
            return
        phase = qcp.phases["search"]
        if isinstance(phase, dict):
            phase.update(updates)
        else:
            for k, v in updates.items():
                setattr(phase, k, v)

    def update_answer_phase(
        self,
        checkpoint: RunCheckpoint,
        question_id: str,
        **updates,
    ) -> None:
        qcp = checkpoint.questions.get(question_id)
        if not qcp:
            return
        phase = qcp.phases["answer"]
        if isinstance(phase, dict):
            phase.update(updates)
        else:
            for k, v in updates.items():
                setattr(phase, k, v)

    def update_evaluate_phase(
        self,
        checkpoint: RunCheckpoint,
        question_id: str,
        **updates,
    ) -> None:
        qcp = checkpoint.questions.get(question_id)
        if not qcp:
            return
        phase = qcp.phases["evaluate"]
        if isinstance(phase, dict):
            phase.update(updates)
        else:
            for k, v in updates.items():
                setattr(phase, k, v)

    # ── Search results persistence ────────────────────────────────────

    def save_search_results(
        self, run_id: str, question_id: str, data: dict
    ) -> str:
        """Save search results to disk. Returns the file path."""
        results_dir = self._results_dir(run_id)
        _ensure_dir(results_dir)
        path = os.path.join(results_dir, f"{question_id}.json")
        self._atomic_write(path, data)
        return path

    def load_search_results(self, run_id: str, question_id: str) -> Optional[dict]:
        """Load search results from disk."""
        path = os.path.join(self._results_dir(run_id), f"{question_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            return json.load(f)

    # ── Report ────────────────────────────────────────────────────────

    def save_report(self, run_id: str, report: dict) -> str:
        """Save the final report. Returns path."""
        path = self._report_path(run_id)
        self._atomic_write(path, report)
        return path

    def load_report(self, run_id: str) -> Optional[dict]:
        path = self._report_path(run_id)
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            return json.load(f)

    # ── Reset ─────────────────────────────────────────────────────────

    def reset_from_phase(self, checkpoint: RunCheckpoint, from_phase: PhaseId) -> None:
        """Reset all phases from the given phase onward for all questions."""
        phases_to_reset = get_phases_from_phase(from_phase)
        for qcp in checkpoint.questions.values():
            if not qcp.phases:
                continue
            for phase_id in phases_to_reset:
                if phase_id == "search":
                    qcp.phases["search"] = SearchPhaseCheckpoint()
                elif phase_id == "answer":
                    qcp.phases["answer"] = AnswerPhaseCheckpoint()
                elif phase_id == "evaluate":
                    qcp.phases["evaluate"] = EvaluatePhaseCheckpoint()
        checkpoint.status = "running"

    # ── Summary ───────────────────────────────────────────────────────

    def get_summary(self, checkpoint: RunCheckpoint) -> Dict[str, Any]:
        """Get a summary of checkpoint progress."""
        total = len(checkpoint.questions)
        searched = answered = evaluated = 0
        for qcp in checkpoint.questions.values():
            if not qcp.phases:
                continue
            s = qcp.phases.get("search")
            a = qcp.phases.get("answer")
            e = qcp.phases.get("evaluate")
            s_status = s.get("status") if isinstance(s, dict) else getattr(s, "status", "pending")
            a_status = a.get("status") if isinstance(a, dict) else getattr(a, "status", "pending")
            e_status = e.get("status") if isinstance(e, dict) else getattr(e, "status", "pending")
            if s_status == "completed":
                searched += 1
            if a_status == "completed":
                answered += 1
            if e_status == "completed":
                evaluated += 1

        return {
            "total": total,
            "searched": searched,
            "answered": answered,
            "evaluated": evaluated,
            "status": checkpoint.status,
        }

    # ── List runs ─────────────────────────────────────────────────────

    def list_runs(self) -> List[Dict[str, Any]]:
        """List all runs with basic metadata."""
        if not os.path.exists(RUNS_DIR):
            return []
        runs = []
        for name in sorted(os.listdir(RUNS_DIR)):
            cp_path = os.path.join(RUNS_DIR, name, "checkpoint.json")
            if os.path.exists(cp_path):
                try:
                    with open(cp_path, "r") as f:
                        data = json.load(f)
                    runs.append({
                        "run_id": data.get("run_id", name),
                        "provider": data.get("provider", ""),
                        "benchmark": data.get("benchmark", ""),
                        "status": data.get("status", ""),
                        "isolation_mode": data.get("isolation_mode", "global"),
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                    })
                except Exception:
                    pass
        return runs

    # ── Serialization ─────────────────────────────────────────────────

    @staticmethod
    def _to_dict(cp: RunCheckpoint) -> dict:
        """Serialize a RunCheckpoint to a JSON-safe dict."""
        data = {
            "run_id": cp.run_id,
            "global_container_tag": cp.global_container_tag,
            "isolation_mode": cp.isolation_mode,
            "status": cp.status,
            "provider": cp.provider,
            "benchmark": cp.benchmark,
            "judge": cp.judge,
            "answering_model": cp.answering_model,
            "created_at": cp.created_at,
            "updated_at": cp.updated_at,
            "limit": cp.limit,
            "sampling": asdict(cp.sampling) if cp.sampling else None,
            "concurrency": asdict(cp.concurrency) if cp.concurrency else None,
            "target_question_ids": cp.target_question_ids,
            "questions": {},
        }
        for qid, qcp in cp.questions.items():
            phases = {}
            if qcp.phases:
                for phase_name, phase_obj in qcp.phases.items():
                    if isinstance(phase_obj, dict):
                        phases[phase_name] = phase_obj
                    else:
                        phases[phase_name] = asdict(phase_obj)
            data["questions"][qid] = {
                "question_id": qcp.question_id,
                "container_tag": qcp.container_tag,
                "question": qcp.question,
                "ground_truth": qcp.ground_truth,
                "question_type": qcp.question_type,
                "question_date": qcp.question_date,
                "phases": phases,
            }
        return data

    @staticmethod
    def _from_dict(data: dict) -> RunCheckpoint:
        """Deserialize a dict to a RunCheckpoint."""
        cp = RunCheckpoint(
            run_id=data["run_id"],
            global_container_tag=data.get("global_container_tag", ""),
            isolation_mode=data.get("isolation_mode", "global"),
            status=data.get("status", "initializing"),
            provider=data.get("provider", ""),
            benchmark=data.get("benchmark", ""),
            judge=data.get("judge", ""),
            answering_model=data.get("answering_model", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            limit=data.get("limit"),
            target_question_ids=data.get("target_question_ids"),
        )
        if data.get("sampling"):
            cp.sampling = SamplingConfig(**data["sampling"])
        if data.get("concurrency"):
            cp.concurrency = ConcurrencyConfig(**data["concurrency"])
        if data.get("provider_config"):
            cp.provider_config = data["provider_config"]

        for qid, qdata in data.get("questions", {}).items():
            phases = qdata.get("phases", {})
            qcp = QuestionCheckpoint(
                question_id=qdata["question_id"],
                container_tag=qdata.get("container_tag", ""),
                question=qdata.get("question", ""),
                ground_truth=qdata.get("ground_truth", ""),
                question_type=qdata.get("question_type", ""),
                question_date=qdata.get("question_date"),
            )
            # Restore phases as dataclasses
            qcp.phases = {
                "search": SearchPhaseCheckpoint(**phases.get("search", {})),
                "answer": AnswerPhaseCheckpoint(**phases.get("answer", {})),
                "evaluate": EvaluatePhaseCheckpoint(**phases.get("evaluate", {})),
            }
            # Restore retrieval_metrics if present
            eval_phase = qcp.phases["evaluate"]
            if eval_phase.retrieval_metrics and isinstance(eval_phase.retrieval_metrics, dict):
                eval_phase.retrieval_metrics = RetrievalMetrics(**eval_phase.retrieval_metrics)
            cp.questions[qid] = qcp

        return cp
