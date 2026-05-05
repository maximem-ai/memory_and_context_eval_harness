"""
Dataset loader for LoCoMo (Long-term Conversational Memory) benchmark.

Source: https://github.com/snap-research/locomo
Paper:  "Evaluating Very Long-Term Conversational Memory of LLM Agents" (ACL 2024)

Downloads via: python scripts/download_datasets.py --dataset locomo
"""

import json
import os
from typing import List, Dict, Any

_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(_DIR, "locomo10.json")

CATEGORY_MAP = {
    1: "single-hop",
    2: "multi-hop",
    3: "temporal",
    4: "open-ended",
    5: "adversarial",
}

# Fallback sample data for smoke tests when real data is not downloaded
SAMPLE_DATA = [
    {
        "session_id": "locomo_sample_01",
        "turns": [
            {"turn_id": "t1", "speaker": "user", "text": "My name is Alice.", "metadata": {}},
            {"turn_id": "t2", "speaker": "assistant", "text": "Hello Alice.", "metadata": {}},
            {"turn_id": "t3", "speaker": "user", "text": "I live in Wonderland.", "metadata": {}},
        ],
        "questions": [
            {"question_id": "q1", "text": "What is my name?", "answer": "Alice", "category": "single-hop", "scorer": "exact_f1"},
            {"question_id": "q2", "text": "Where do I live?", "answer": "Wonderland", "category": "single-hop", "scorer": "exact_f1"},
        ],
    },
    {
        "session_id": "locomo_sample_02",
        "turns": [
            {"turn_id": "t1", "speaker": "user", "text": "The secret code is 1234.", "metadata": {}},
        ],
        "questions": [
            {"question_id": "q1", "text": "What is the code?", "answer": "1234", "category": "single-hop", "scorer": "exact_f1"},
        ],
    },
]


def _parse_sessions(conversation: dict) -> List[dict]:
    """Extract numbered sessions from a LoCoMo conversation object."""
    sessions = []
    i = 1
    while f"session_{i}" in conversation:
        sessions.append({
            "session_number": i,
            "session_id": f"session_{i}",
            "timestamp": conversation.get(f"session_{i}_date_time", ""),
            "raw_turns": conversation[f"session_{i}"],
            "speaker_a": conversation.get("speaker_a", "Person A"),
            "speaker_b": conversation.get("speaker_b", "Person B"),
        })
        i += 1
    return sessions


def _convert_record(record_idx: int, record: dict) -> dict:
    """Convert a single LoCoMo record to runner format."""
    conv = record["conversation"]
    qa_list = record.get("qa", [])
    parsed_sessions = _parse_sessions(conv)

    cumulative_turns: List[Dict[str, Any]] = []
    for sess in parsed_sessions:
        for turn in sess["raw_turns"]:
            raw_speaker = turn.get("speaker", "speaker_a")
            # Map speaker_a/speaker_b or actual names to user/assistant
            is_speaker_a = (
                raw_speaker == "speaker_a"
                or raw_speaker == sess["speaker_a"]
            )
            speaker = "user" if is_speaker_a else "assistant"

            # LoCoMo turns may include image attachments. Concatenate the
            # BLIP caption (and search query) into the text so memory systems
            # actually receive the image content. Mirrors the official
            # mem0/memory-benchmarks loader behavior.
            text = turn.get("text") or ""
            blip = turn.get("blip_caption") or ""
            query = turn.get("query") or ""
            if query and blip:
                photo_tag = f"[Sharing image — query: {query}. The image shows: {blip}]"
            elif query:
                photo_tag = f"[Sharing image — query for: {query}]"
            elif blip:
                photo_tag = f"[Sharing image that shows: {blip}]"
            else:
                photo_tag = ""
            if photo_tag:
                text = f"{text} {photo_tag}".strip() if text else photo_tag

            cumulative_turns.append({
                "turn_id": turn["dia_id"],
                "speaker": speaker,
                "text": text,
                "metadata": {
                    "session_id": f"locomo_{record_idx}_session_{sess['session_number']}",
                    "session_name": f"Session {sess['session_number']}",
                    "timestamp": sess["timestamp"],
                    "original_speaker": turn["speaker"],
                    "speaker_a_name": sess["speaker_a"],
                    "speaker_b_name": sess["speaker_b"],
                },
            })

    questions = []
    for qi, qa in enumerate(qa_list):
        cat_num = qa.get("category", 0)
        # Adversarial questions (category 5) have no "answer" — the correct
        # response is that the question is unanswerable.  We store the
        # adversarial_answer so scorers can use it if needed.
        answer = qa.get("answer", "")
        adversarial_answer = qa.get("adversarial_answer", "")
        questions.append({
            "question_id": f"locomo_{record_idx}_q{qi}",
            "text": qa["question"],
            "answer": str(answer) if answer else "unanswerable",
            "category": CATEGORY_MAP.get(cat_num, f"category_{cat_num}"),
            "scorer": "exact_f1",
            "adversarial_answer": adversarial_answer,
        })

    return {
        "session_id": f"locomo_{record_idx}",
        "turns": cumulative_turns,
        "questions": questions,
    }


def load_dataset(path: str = None) -> List[Dict[str, Any]]:
    """
    Load LoCoMo dataset.

    Returns list of samples in runner format. Falls back to SAMPLE_DATA
    if the real data file hasn't been downloaded yet.
    """
    data_path = path or DATA_FILE
    if not os.path.exists(data_path):
        return SAMPLE_DATA

    with open(data_path, "r") as f:
        raw = json.load(f)

    return [_convert_record(i, record) for i, record in enumerate(raw)]


def load_all_turns(max_records: int = 0) -> List[Dict[str, Any]]:
    """Load all turns across all records as a flat list.

    Args:
        max_records: Limit to first N records (0 = all).
    """
    samples = load_dataset()
    if max_records > 0:
        samples = samples[:max_records]
    all_turns = []
    for sample in samples:
        all_turns.extend(sample.get("turns", []))
    return all_turns


def load_questions(max_records: int = 0) -> List[Dict[str, Any]]:
    """Load all evaluation questions as a flat list.

    Args:
        max_records: Limit to first N records (0 = all).
    """
    samples = load_dataset()
    if max_records > 0:
        samples = samples[:max_records]
    all_questions = []
    for sample in samples:
        all_questions.extend(sample.get("questions", []))
    return all_questions
