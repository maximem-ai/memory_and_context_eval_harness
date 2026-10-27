"""LongMemEval-S loader.

One Synap user per question. That is not a convenience: LongMemEval's whole
construction is that the evidence for question X sits in a haystack of
distractor sessions, and several questions reuse overlapping session content.
Sharing a scope across questions would let one question's haystack answer
another's, which inflates the score without measuring anything.

The published gold answers are used exactly as shipped.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_DIR))
DEFAULT_DATA = os.path.join(
    REPO_ROOT, "datasets", "longmemeval", "longmemeval_s_cleaned.json")

# The S split. validate() refuses anything else: the other splits share these
# question ids and the question-type mix, and differ only in haystack size, so
# the question count alone would not notice a wrong file.
EXPECTED_QUESTIONS = 500
EXPECTED_SESSIONS = 23_867
EXPECTED_TURNS = 246_750
EXPECTED_TYPES = {
    "knowledge-update": 78,
    "multi-session": 133,
    "single-session-assistant": 56,
    "single-session-preference": 30,
    "single-session-user": 70,
    "temporal-reasoning": 133,
}
EXPECTED_ABSTENTION = 30

# ---------------------------------------------------------------------------
# Scope prefix.
#
# Every Synap user id this benchmark touches is built here and nowhere else.
# Use a NEW prefix for every fresh run. Synap caches extraction by content
# hash, and that cache survives server-side deletion of a user's data, so
# re-ingesting the same text under the same user id can return an earlier
# result instead of extracting again. A new user id is a cache miss.
#
# The prefix is a property of the cohort, so the SAME value must be used at
# ingest and at retrieval. Passing it to one and not the other points the
# search phase at scopes that were never written, which does not error: it
# returns nothing, on every question, and reads as a catastrophic score
# rather than as a misconfiguration. run.py guards against that explicitly.
DEFAULT_USER_PREFIX = "lme_"
USER_PREFIX = DEFAULT_USER_PREFIX

_PREFIX_OK = re.compile(r"^[a-z0-9_]+$")


def checkpoint_tag(prefix: str, tag: str) -> str:
    """Namespace a checkpoint tag by cohort prefix.

    A rotation recorded for a wedged scope under one prefix must never be
    applied to a scope under another.
    """
    return f"{prefix.rstrip('_')}_{tag}"


def set_user_prefix(prefix: str) -> str:
    """Set the scope prefix for this process. Returns the value applied."""
    global USER_PREFIX
    if not prefix or not _PREFIX_OK.match(prefix):
        raise ValueError(
            f"user prefix {prefix!r} must be non-empty and match [a-z0-9_]+: "
            "uppercase letters and hyphens are not accepted in scope ids")
    USER_PREFIX = prefix
    return USER_PREFIX


@dataclass
class Session:
    session_id: str
    date: Optional[str]
    turns: List[Dict[str, Any]]


@dataclass
class Question:
    question_id: str
    question_type: str
    question: str
    answer: str
    question_date: Optional[str]
    sessions: List[Session] = field(default_factory=list)
    answer_session_ids: List[str] = field(default_factory=list)

    @property
    def user_id(self) -> str:
        # Deterministic and collision-free: question_ids are unique in the
        # split. Read at access time, not at construction, so
        # set_user_prefix() applies to questions that are already loaded.
        return f"{USER_PREFIX}{self.question_id}"

    @property
    def is_abstention(self) -> bool:
        return "_abs" in self.question_id


def load(path: str = DEFAULT_DATA, limit: Optional[int] = None) -> List[Question]:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"dataset not found: {path}. Download it with "
            "`python scripts/download_datasets.py --dataset longmemeval "
            "--variant s`")
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    out: List[Question] = []
    for rec in raw:
        dates = rec.get("haystack_dates") or []
        sids = rec.get("haystack_session_ids") or []
        sessions = []
        for i, turns in enumerate(rec.get("haystack_sessions") or []):
            sessions.append(Session(
                session_id=sids[i] if i < len(sids) else f"s{i}",
                date=dates[i] if i < len(dates) else None,
                turns=turns or [],
            ))
        out.append(Question(
            question_id=rec["question_id"],
            question_type=rec["question_type"],
            question=rec["question"],
            answer=str(rec.get("answer", "")),
            question_date=rec.get("question_date"),
            sessions=sessions,
            answer_session_ids=rec.get("answer_session_ids") or [],
        ))
    return out[:limit] if limit else out


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def validate(questions: List[Question]) -> Dict[str, Any]:
    """Check that the loaded file is exactly the LongMemEval S split."""
    types = dict(collections.Counter(q.question_type for q in questions))
    abst = sum(1 for q in questions if q.is_abstention)
    report: Dict[str, Any] = {
        "questions": len(questions),
        "by_question_type": dict(sorted(types.items())),
        "abstention": abst,
        "sessions": sum(len(q.sessions) for q in questions),
        "turns": sum(len(s.turns) for q in questions for s in q.sessions),
        "unique_user_ids": len({q.user_id for q in questions}),
    }
    problems = []
    if len(questions) != EXPECTED_QUESTIONS:
        problems.append(
            f"expected {EXPECTED_QUESTIONS} questions, got {len(questions)}")
    if types != EXPECTED_TYPES:
        problems.append(f"question-type mix differs from the S split: {types}")
    if abst != EXPECTED_ABSTENTION:
        problems.append(
            f"expected {EXPECTED_ABSTENTION} abstention questions, got {abst}")
    if report["sessions"] != EXPECTED_SESSIONS:
        problems.append(
            f"expected {EXPECTED_SESSIONS} sessions, got {report['sessions']}"
            " (is this the S split?)")
    if report["turns"] != EXPECTED_TURNS:
        problems.append(
            f"expected {EXPECTED_TURNS} turns, got {report['turns']}"
            " (is this the S split?)")
    if report["unique_user_ids"] != len(questions):
        problems.append(
            "user_ids are not unique per question: isolation would be broken")
    report["problems"] = problems
    report["ok"] = not problems
    return report


def select(questions: List[Question],
           include_types: Optional[List[str]] = None,
           exclude_types: Optional[List[str]] = None,
           exclude_abstention: bool = False,
           include_ids: Optional[List[str]] = None) -> tuple:
    """Narrow the split to the questions this pass covers.

    Deliberately separate from load()/validate(): validate() checks the whole
    file, and that check must run over all of it. Selection happens after, so
    a filtered pass still proves its corpus is intact.

    exclude_abstention drops the 30 unanswerable questions. They are not a
    question_type: they are spread across four types and identified only by
    the "_abs" id suffix, so --exclude-question-types cannot reach them.

    Returns (subset, record). The record goes into the manifest verbatim: a
    filtered run's accuracy is over a different denominator than a full
    run's, and nothing downstream can tell the difference without it.
    """
    known = set(EXPECTED_TYPES)
    for name, given in (("--question-types", include_types),
                        ("--exclude-question-types", exclude_types)):
        for t in given or []:
            if t not in known:
                raise ValueError(
                    f"{name}: {t!r} is not a LongMemEval question type. "
                    f"Known types: {', '.join(sorted(known))}")

    subset = questions
    if include_ids:
        # An id that is not in the split is a typo, not an empty result: a run
        # that silently evaluates 9 of 10 requested questions reports a score
        # over a denominator nobody chose.
        wanted = list(dict.fromkeys(include_ids))
        present = {q.question_id for q in questions}
        missing = [i for i in wanted if i not in present]
        if missing:
            raise ValueError(
                f"--question-ids: {len(missing)} id(s) not in the split: "
                f"{', '.join(missing[:10])}")
        subset = [q for q in subset if q.question_id in set(wanted)]
    if include_types:
        subset = [q for q in subset if q.question_type in set(include_types)]
    if exclude_types:
        subset = [q for q in subset if q.question_type not in set(exclude_types)]
    if exclude_abstention:
        subset = [q for q in subset if not q.is_abstention]

    record = {
        "include_question_types": sorted(include_types) if include_types else None,
        "exclude_question_types": sorted(exclude_types) if exclude_types else None,
        "exclude_abstention": bool(exclude_abstention),
        "include_ids": list(include_ids) if include_ids else None,
        "selected_questions": len(subset),
        "selected_answerable": sum(1 for q in subset if not q.is_abstention),
        "selected_abstention": sum(1 for q in subset if q.is_abstention),
        "selected_sessions": sum(len(q.sessions) for q in subset),
        "by_question_type": dict(sorted(collections.Counter(
            q.question_type for q in subset).items())),
        "excluded_questions": len(questions) - len(subset),
        "is_partial": len(subset) != len(questions),
    }
    return subset, record


def fingerprint(questions: List[Question]) -> str:
    """Stable hash of exactly what must be ingested.

    Covers every question id and every session id, in order, so a checkpoint
    built against a different split (or a re-ordered one) cannot be silently
    resumed against this one.
    """
    h = hashlib.sha256()
    for q in questions:
        h.update(q.question_id.encode())
        for s in q.sessions:
            h.update(b"|")
            h.update(s.session_id.encode())
    return h.hexdigest()
