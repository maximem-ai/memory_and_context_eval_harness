"""
LongMemEval Benchmark — wraps the existing loader behind the Benchmark interface.

LongMemEval has one question per record, with explicit question→session mapping
via haystack_session_ids. Many questions share the same haystack sessions.
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
    "single-session": QuestionTypeInfo(
        id="single-session",
        alias="Single-Session",
        description="Questions answerable from a single session",
    ),
    "multi-session": QuestionTypeInfo(
        id="multi-session",
        alias="Multi-Session",
        description="Questions requiring information from multiple sessions",
    ),
    "temporal": QuestionTypeInfo(
        id="temporal",
        alias="Temporal",
        description="Questions about time, dates, or temporal ordering",
    ),
    "knowledge-update": QuestionTypeInfo(
        id="knowledge-update",
        alias="Knowledge Update",
        description="Questions about information that was updated over time",
    ),
    "single-session-preference": QuestionTypeInfo(
        id="single-session-preference",
        alias="Single-Session Preference",
        description="Questions about user preferences from a single session",
    ),
    "abstention": QuestionTypeInfo(
        id="abstention",
        alias="Abstention",
        description="Questions that should be answered with 'I don't know'",
    ),
}


class LongMemEvalBenchmark(Benchmark):
    name = "longmemeval"

    def __init__(self):
        self._questions: List[UnifiedQuestion] = []
        self._sessions: Dict[str, UnifiedSession] = {}
        self._question_to_sessions: Dict[str, List[str]] = {}

    async def load(self, config: Optional[BenchmarkConfig] = None) -> None:
        from datasets.longmemeval.loader import load_question_session_map, load_questions

        question_to_sessions, session_data = load_question_session_map()
        raw_questions = load_questions()

        # Build UnifiedSession objects from session_data
        for sid, sdata in session_data.items():
            messages = []
            for turn in sdata.get("turns", []):
                role = "user" if turn["speaker"] == "user" else "assistant"
                messages.append(
                    UnifiedMessage(
                        role=role,
                        content=turn["text"],
                        timestamp=turn.get("metadata", {}).get("timestamp"),
                    )
                )
            self._sessions[sid] = UnifiedSession(
                session_id=sid,
                messages=messages,
                metadata={
                    "session_name": sdata.get("name", f"Session {sid}"),
                },
            )

        # Build UnifiedQuestion objects
        for q in raw_questions:
            qid = q["question_id"]
            haystack_ids = question_to_sessions.get(qid, [])
            self._question_to_sessions[qid] = haystack_ids

            self._questions.append(
                UnifiedQuestion(
                    question_id=qid,
                    question=q["text"],
                    question_type=q.get("category", ""),
                    ground_truth=q["answer"],
                    haystack_session_ids=haystack_ids,
                    metadata={
                        "question_date": q.get("question_date"),
                    },
                )
            )

        logger.info(
            "LongMemEval loaded: %d questions, %d sessions",
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
