"""
LoCoMo Benchmark — wraps the existing loader behind the Benchmark interface.

LoCoMo has multiple questions per conversation record. All turns in a record
are relevant to all questions in that record.
"""

import logging
from typing import Dict, List, Optional

from runner.types import (
    Benchmark,
    BenchmarkConfig,
    QuestionFilter,
    QuestionTypeInfo,
    QuestionTypeRegistry,
    UnifiedMessage,
    UnifiedQuestion,
    UnifiedSession,
)

logger = logging.getLogger(__name__)

QUESTION_TYPES: QuestionTypeRegistry = {
    "single-hop": QuestionTypeInfo(
        id="single-hop",
        alias="Single-Hop",
        description="Questions requiring a single fact retrieval",
    ),
    "multi-hop": QuestionTypeInfo(
        id="multi-hop",
        alias="Multi-Hop",
        description="Questions requiring combining multiple facts",
    ),
    "temporal": QuestionTypeInfo(
        id="temporal",
        alias="Temporal",
        description="Questions about when events occurred or temporal ordering",
    ),
    "open-ended": QuestionTypeInfo(
        id="open-ended",
        alias="Open-Ended",
        description="Questions requiring broader reasoning over context",
    ),
    "adversarial": QuestionTypeInfo(
        id="adversarial",
        alias="Adversarial",
        description="Questions designed to be unanswerable from context",
    ),
}


class LoComoBenchmark(Benchmark):
    name = "locomo"

    def __init__(self):
        self._questions: List[UnifiedQuestion] = []
        self._sessions: Dict[str, UnifiedSession] = {}
        self._question_to_sessions: Dict[str, List[str]] = {}

    async def load(self, config: Optional[BenchmarkConfig] = None) -> None:
        from datasets.locomo.loader import load_all_turns, load_questions

        raw_turns = load_all_turns()
        raw_questions = load_questions()

        # Build sessions from turns grouped by session_id in metadata
        session_turns: Dict[str, List] = {}
        session_meta: Dict[str, Dict] = {}
        record_sessions: Dict[str, List[str]] = {}  # record_prefix -> [session_ids]

        for turn in raw_turns:
            meta = turn.get("metadata", {})
            sid = meta.get("session_id", turn.get("session_id", ""))
            if not sid:
                continue

            if sid not in session_turns:
                session_turns[sid] = []
                session_meta[sid] = {
                    "session_name": meta.get("session_name", ""),
                    "timestamp": meta.get("timestamp"),
                    "speaker_a_name": meta.get("speaker_a_name"),
                    "speaker_b_name": meta.get("speaker_b_name"),
                }

            role = "user" if turn["speaker"] == "user" else "assistant"
            session_turns[sid].append(
                UnifiedMessage(
                    role=role,
                    content=turn["text"],
                    timestamp=meta.get("timestamp"),
                    speaker=meta.get("original_speaker"),
                )
            )

            # Track which record this session belongs to
            # session_id format: locomo_{record_idx}_session_{n}
            # record prefix: locomo_{record_idx}
            parts = sid.split("_session_")
            if parts:
                record_prefix = parts[0]
                record_sessions.setdefault(record_prefix, [])
                if sid not in record_sessions[record_prefix]:
                    record_sessions[record_prefix].append(sid)

        # Create UnifiedSession objects
        for sid, turns in session_turns.items():
            meta = session_meta.get(sid, {})
            self._sessions[sid] = UnifiedSession(
                session_id=sid,
                messages=turns,
                metadata=meta,
            )

        # Create UnifiedQuestion objects
        for q in raw_questions:
            qid = q["question_id"]
            # question_id format: locomo_{record_idx}_q{n}
            # Map to record prefix: locomo_{record_idx}
            parts = qid.rsplit("_q", 1)
            record_prefix = parts[0] if parts else ""

            haystack_ids = record_sessions.get(record_prefix, [])
            self._question_to_sessions[qid] = haystack_ids

            self._questions.append(
                UnifiedQuestion(
                    question_id=qid,
                    question=q["text"],
                    question_type=q.get("category", ""),
                    ground_truth=q["answer"],
                    haystack_session_ids=haystack_ids,
                    metadata={
                        "adversarial_answer": q.get("adversarial_answer"),
                    },
                )
            )

        logger.info(
            "LoCoMo loaded: %d questions, %d sessions",
            len(self._questions),
            len(self._sessions),
        )

    def get_questions(self, filter: Optional[QuestionFilter] = None) -> List[UnifiedQuestion]:
        questions = self._questions
        if filter:
            if filter.question_types:
                questions = [q for q in questions if q.question_type in filter.question_types]
            if filter.offset:
                questions = questions[filter.offset:]
            if filter.limit:
                questions = questions[:filter.limit]
        return questions

    def get_haystack_sessions(self, question_id: str) -> List[UnifiedSession]:
        session_ids = self._question_to_sessions.get(question_id, [])
        return [self._sessions[sid] for sid in session_ids if sid in self._sessions]

    def get_ground_truth(self, question_id: str) -> str:
        for q in self._questions:
            if q.question_id == question_id:
                return q.ground_truth
        return ""

    def get_question_types(self) -> QuestionTypeRegistry:
        return QUESTION_TYPES

    def get_question_group_id(self, question_id: str) -> str:
        # All questions in a record share the same haystack — group by record
        # prefix so isolated-mode ingestion happens once per record.
        parts = question_id.rsplit("_q", 1)
        return parts[0] if parts else question_id
