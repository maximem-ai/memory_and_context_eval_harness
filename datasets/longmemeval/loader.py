"""
Dataset loader for LongMemEval benchmark.

Source: https://github.com/xiaowu0162/LongMemEval
Data:   https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
Paper:  "Benchmarking Chat Assistants on Long-Term Interactive Memory" (ICLR 2025)

Downloads via: python scripts/download_datasets.py --dataset longmemeval
"""

import json
import os
from typing import List, Dict, Any, Optional

_DIR = os.path.dirname(os.path.abspath(__file__))

# Auto-detect in preference order
_VARIANTS = [
    "longmemeval_interleaved.json",
    "longmemeval_s_cleaned.json",
    "longmemeval_m_cleaned.json",
]

# Fallback sample data for smoke tests when real data is not downloaded
SAMPLE_DATA = [
    {
        "session_id": "lme_sample_01",
        "turns": [
            {"turn_id": "t1", "speaker": "user", "text": "I put the keys on the table at 10:00 AM.", "metadata": {"timestamp": "10:00"}},
            {"turn_id": "t2", "speaker": "user", "text": "I moved them to the drawer at 10:05 AM.", "metadata": {"timestamp": "10:05"}},
        ],
        "questions": [
            {"question_id": "q1", "text": "Where are the keys now?", "answer": "drawer", "category": "knowledge-update", "scorer": "exact_f1"},
        ],
    }
]


def _find_data_file() -> Optional[str]:
    """Auto-detect the best available data file."""
    for variant in _VARIANTS:
        p = os.path.join(_DIR, variant)
        if os.path.exists(p):
            return p
    return None


def _convert_record(record: dict) -> dict:
    """Convert a single LongMemEval record to runner format.

    LongMemEval structure: haystack_sessions is a list of lists (each inner
    list = one session's turns), with parallel haystack_session_ids for IDs.
    """
    qid = record["question_id"]
    session_ids = record.get("haystack_session_ids", [])
    sessions = record.get("haystack_sessions", [])
    haystack_dates = record.get("haystack_dates", [])

    # Flatten haystack_sessions into turns
    all_turns: List[Dict[str, Any]] = []
    for si, sess_turns in enumerate(sessions):
        sess_id = session_ids[si] if si < len(session_ids) else f"session_{si}"
        sess_date = haystack_dates[si] if si < len(haystack_dates) else None
        for ti, turn in enumerate(sess_turns):
            meta: Dict[str, Any] = {
                "session_id": sess_id,
                "session_name": f"Session {sess_id}",
            }
            # Attach session date to first turn so orchestrator can set document_created_at
            if ti == 0 and sess_date:
                meta["timestamp"] = sess_date
            all_turns.append({
                "turn_id": f"{sess_id}_t{ti}",
                "speaker": turn["role"],  # already "user"/"assistant"
                "text": turn["content"],
                "metadata": meta,
            })

    questions = [{
        "question_id": qid,
        "text": record["question"],
        "answer": str(record["answer"]),
        "category": record.get("question_type", ""),
        "scorer": "exact_f1",
    }]

    return {
        "session_id": qid,
        "turns": all_turns,
        "questions": questions,
    }


def load_dataset(path: str = None) -> List[Dict[str, Any]]:
    """
    Load LongMemEval dataset.

    Returns list of samples in runner format. Each LongMemEval question
    becomes an independent sample with its haystack sessions as turns.
    Falls back to SAMPLE_DATA if no data file is found.
    """
    if path and os.path.exists(path):
        data_path = path
    else:
        data_path = _find_data_file()
        if not data_path:
            return SAMPLE_DATA

    with open(data_path, "r") as f:
        raw = json.load(f)

    return [_convert_record(record) for record in raw]


def load_all_turns(max_records: int = 0) -> List[Dict[str, Any]]:
    """
    Load all unique turns across all records (deduplicated by session_id).

    Many LongMemEval questions share the same haystack sessions, so we
    deduplicate to avoid feeding the same content multiple times.

    Args:
        max_records: Limit to first N records (0 = all).
    """
    data_path = _find_data_file()
    if not data_path:
        return [t for s in SAMPLE_DATA for t in s.get("turns", [])]

    with open(data_path, "r") as f:
        raw = json.load(f)

    if max_records > 0:
        raw = raw[:max_records]

    seen_sessions: set = set()
    all_turns: List[Dict[str, Any]] = []
    for record in raw:
        session_ids = record.get("haystack_session_ids", [])
        sessions = record.get("haystack_sessions", [])
        haystack_dates = record.get("haystack_dates", [])
        for si, sess_turns in enumerate(sessions):
            sid = session_ids[si] if si < len(session_ids) else f"session_{si}"
            if sid in seen_sessions:
                continue
            seen_sessions.add(sid)
            sess_date = haystack_dates[si] if si < len(haystack_dates) else None
            for ti, turn in enumerate(sess_turns):
                meta: Dict[str, Any] = {
                    "session_id": sid,
                    "session_name": f"Session {sid}",
                }
                # Attach session date to first turn so orchestrator can set document_created_at
                if ti == 0 and sess_date:
                    meta["timestamp"] = sess_date
                all_turns.append({
                    "turn_id": f"{sid}_t{ti}",
                    "speaker": turn["role"],
                    "text": turn["content"],
                    "metadata": meta,
                })
    return all_turns


def load_question_session_map(max_records: int = 0) -> tuple:
    """Load per-question session mapping for isolated eval mode.

    Returns:
        (question_to_sessions, session_data) where:
        - question_to_sessions: {question_id: [session_id, ...]}
        - session_data: {session_id: {name: str, turns: [...]}}
    """
    data_path = _find_data_file()
    if not data_path:
        return {}, {}

    with open(data_path, "r") as f:
        raw = json.load(f)

    if max_records > 0:
        raw = raw[:max_records]

    question_to_sessions: Dict[str, List[str]] = {}
    session_data: Dict[str, Dict[str, Any]] = {}

    for record in raw:
        qid = record["question_id"]
        session_ids = record.get("haystack_session_ids", [])
        sessions = record.get("haystack_sessions", [])
        haystack_dates = record.get("haystack_dates", [])

        question_to_sessions[qid] = list(session_ids)

        for si, sess_turns in enumerate(sessions):
            sid = session_ids[si] if si < len(session_ids) else f"session_{si}"
            if sid in session_data:
                continue  # already built from a previous question
            sess_date = haystack_dates[si] if si < len(haystack_dates) else None
            turns = []
            for ti, turn in enumerate(sess_turns):
                meta: Dict[str, Any] = {
                    "session_id": sid,
                    "session_name": f"Session {sid}",
                }
                if ti == 0 and sess_date:
                    meta["timestamp"] = sess_date
                turns.append({
                    "turn_id": f"{sid}_t{ti}",
                    "speaker": turn["role"],
                    "text": turn["content"],
                    "metadata": meta,
                })
            session_data[sid] = {"name": f"Session {sid}", "turns": turns}

    return question_to_sessions, session_data


def load_questions(max_records: int = 0) -> List[Dict[str, Any]]:
    """Load all evaluation questions as a flat list."""
    data_path = _find_data_file()
    if not data_path:
        return [q for s in SAMPLE_DATA for q in s.get("questions", [])]

    with open(data_path, "r") as f:
        raw = json.load(f)

    if max_records > 0:
        raw = raw[:max_records]

    return [
        {
            "question_id": r["question_id"],
            "text": r["question"],
            "answer": str(r["answer"]),
            "category": r.get("question_type", ""),
            "question_date": r.get("question_date", ""),
            "scorer": "exact_f1",
        }
        for r in raw
    ]
