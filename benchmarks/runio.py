"""Per-run persistence for the LongMemEval-S runner.

Layout: results/{benchmark}/{run_id}/ holding manifest.json, questions.jsonl,
by_category.json, summary.json and REPORT.md. questions.jsonl is append-only
and flushed per row, so a run that dies partway still leaves every question it
finished: a partial run we can count beats a complete run we lost.

A question that errored is written too, so the failure is on record. When a
run is resumed the question is tried again and a later row is appended;
read_rows() returns the latest row per question, so a retried question is
counted once, by its most recent attempt.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_dir(results_root: str, benchmark: str, run_id: str) -> str:
    d = os.path.join(results_root, benchmark, run_id)
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class QuestionRow:
    question_id: str
    user_id: str
    question_type: str
    question: str
    gold_answer: str
    abstention: bool = False
    generated_answer: str = ""

    # Retrieval kept in full so a wrong answer can be diagnosed as a retrieval
    # miss rather than a generation miss without re-running anything.
    retrieved_context: List[Dict[str, Any]] = field(default_factory=list)
    retrieved_count: int = 0
    retrieved_content_tokens: int = 0
    budget_truncated: bool = False
    budget_limit_tokens: int = 0
    retrieval_empty: bool = False
    # 'cloud' or 'cache', as the server reported it. A row that says 'cache'
    # re-read an earlier response, so it measures that response rather than
    # a fresh retrieval.
    retrieval_source: Optional[str] = None
    retrieval_correlation_id: Optional[str] = None
    retrieval_attempts: int = 1
    # What the server said about the health of this fetch: null on a healthy
    # one, else {degraded, level, warnings}.
    retrieval_health: Optional[Dict[str, Any]] = None
    # Set when the future-date guard refused to score this row: the offending
    # metadata values, so the leak is diagnosable without re-running.
    future_dated_metadata: Optional[list] = None

    # Verbatim trace: the exact text sent to each model, not a summary of it.
    answer_prompt: str = ""
    judge_prompt: str = ""
    # The judge's raw reply. The verdict is derived from it ('yes' in the
    # reply) when results are aggregated; it is not stored separately.
    judge_raw: str = ""
    ingested_session_ids: List[str] = field(default_factory=list)
    ingested_doc_ref: str = ""       # path to the verbatim ingested documents

    search_ms: float = 0.0
    answer_ms: float = 0.0
    judge_ms: float = 0.0
    answer_prompt_tokens: int = 0
    answer_completion_tokens: int = 0
    judge_prompt_tokens: int = 0
    judge_completion_tokens: int = 0

    error: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


class QuestionWriter:
    def __init__(self, directory: str):
        self.path = os.path.join(directory, "questions.jsonl")
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, row: QuestionRow) -> None:
        self._fh.write(row.to_json() + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def read_rows(directory: str) -> List[Dict[str, Any]]:
    """The latest row per question, in first-seen order."""
    path = os.path.join(directory, "questions.jsonl")
    if not os.path.exists(path):
        return []
    latest: Dict[str, Dict[str, Any]] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn final line after a kill
            latest[row.get("question_id", "")] = row
    return list(latest.values())


def write_json(directory: str, name: str, payload: Any) -> str:
    path = os.path.join(directory, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)
    return path


def write_manifest(directory: str, manifest: Dict[str, Any]) -> str:
    return write_json(directory, "manifest.json", manifest)


def timing_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def pct(vals, p):
        if not vals:
            return None
        s = sorted(vals)
        return round(s[min(len(s) - 1, int(len(s) * p))], 1)

    search = [r.get("search_ms", 0) for r in rows if r.get("search_ms")]
    answer = [r.get("answer_ms", 0) for r in rows if r.get("answer_ms")]
    judge = [r.get("judge_ms", 0) for r in rows if r.get("judge_ms")]
    return {
        "search_ms": {"p50": pct(search, 0.5), "p95": pct(search, 0.95),
                      "mean": round(sum(search) / len(search), 1) if search else None},
        "answer_ms": {"p50": pct(answer, 0.5), "p95": pct(answer, 0.95)},
        "judge_ms": {"p50": pct(judge, 0.5), "p95": pct(judge, 0.95)},
    }


def retrieval_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows) or 1
    toks = [r.get("retrieved_content_tokens", 0) for r in rows]
    ptoks = [r.get("answer_prompt_tokens") or 0 for r in rows]
    return {
        "questions": len(rows),
        "empty_retrievals": sum(1 for r in rows if r.get("retrieval_empty")),
        "empty_pct": round(100 * sum(1 for r in rows if r.get("retrieval_empty")) / n, 2),
        "budget_truncated": sum(1 for r in rows if r.get("budget_truncated")),
        "mean_items": round(sum(r.get("retrieved_count", 0) for r in rows) / n, 1),
        # Two different quantities. memory_field_tokens is the budget
        # instrument: the server's cap applies to the `memory` field, so
        # truncation is judged against this. It is NOT what the reader sees.
        # source_evidence is emitted in full alongside each memory and can
        # dwarf it, so a report that quotes only this number understates the
        # real prompt.
        "memory_field_tokens": {
            "mean": round(sum(toks) / n, 1) if toks else 0,
            "peak": max(toks) if toks else 0,
        },
        "answer_prompt_tokens": {
            "mean": round(sum(ptoks) / n, 1) if ptoks else 0,
            "peak": max(ptoks) if ptoks else 0,
        },
        "errors": sum(1 for r in rows if r.get("error")),
    }
