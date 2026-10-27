"""LongMemEval's own judge, ported from the vendored upstream.

Source: upstream/src/evaluation/evaluate_qa.py at the commit in
upstream/PINNED_COMMIT. tests/unit/test_longmemeval_s.py compares the prompts
built here against that file.

Two things about this judge are easy to get wrong and both change the score:

1. The prompt is **question-type specific**. temporal-reasoning explicitly
   forgives off-by-one day errors; knowledge-update explicitly accepts an
   answer that also recites the superseded value. Scoring every type with one
   generic prompt would silently move the number.
2. Abstention questions are selected by `'_abs' in question_id` and judged
   against a completely different prompt that asks whether the model
   *correctly refused*. There are 30 of them in the S split. Judged with the
   normal prompt they would all score wrong.

The upstream call is one request per question: temperature 0, max_tokens 10,
no system prompt, and the label is the substring test `'yes' in
response.lower()`. That substring test is kept as-is rather than "fixed" into
an equality check, because parity with the published method matters more than
our opinion of the code.

The judge receives the full generated answer text, including the "Answer:"
line and any explanation that follows it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

# Upstream's model_zoo. The paper's headline numbers use gpt-4o.
MODEL_ZOO = {
    "gpt-4o-mini": "gpt-4o-mini-2024-07-18",
    "gpt-4o": "gpt-4o-2024-08-06",
}

QUESTION_TYPES = [
    "single-session-user",
    "single-session-assistant",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
    "single-session-preference",
]

_GENERIC = (
    "I will give you a question, a correct answer, and a response from a model. "
    "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
    "If the response is equivalent to the correct answer or contains all the intermediate "
    "steps to get the correct answer, you should also answer yes. If the response only "
    "contains a subset of the information required by the answer, answer no. "
    "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)

_TEMPORAL = (
    "I will give you a question, a correct answer, and a response from a model. "
    "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
    "If the response is equivalent to the correct answer or contains all the intermediate "
    "steps to get the correct answer, you should also answer yes. If the response only "
    "contains a subset of the information required by the answer, answer no. In addition, "
    "do not penalize off-by-one errors for the number of days. If the question asks for the "
    "number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., "
    "predicting 19 days when the answer is 18), the model's response is still correct. "
    "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)

_KNOWLEDGE_UPDATE = (
    "I will give you a question, a correct answer, and a response from a model. "
    "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
    "If the response contains some previous information along with an updated answer, the "
    "response should be considered as correct as long as the updated answer is the required "
    "answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)

_PREFERENCE = (
    "I will give you a question, a rubric for desired personalized response, and a response "
    "from a model. Please answer yes if the response satisfies the desired response. "
    "Otherwise, answer no. The model does not need to reflect all the points in the rubric. "
    "The response is correct as long as it recalls and utilizes the user's personal "
    "information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)

_ABSTENTION = (
    "I will give you an unanswerable question, an explanation, and a response from a model. "
    "Please answer yes if the model correctly identifies the question as unanswerable. The "
    "model could say that the information is incomplete, or some other information is given "
    "but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\n"
    "Model Response: {}\n\nDoes the model correctly identify the question as unanswerable? "
    "Answer yes or no only."
)


class EmptyJudgeReply(RuntimeError):
    """The judge returned no text, so no verdict exists.

    Reasoning models can spend the whole max_tokens=10 budget on reasoning and
    return an empty string. Read with the substring test that is "no", which
    would score a correct answer as wrong. It is an error row instead.
    """


def is_abstention(question_id: str) -> bool:
    """Upstream's selector, verbatim: `abstention='_abs' in entry['question_id']`."""
    return "_abs" in question_id


def get_anscheck_prompt(task: str, question: str, answer: str, response: str,
                        abstention: bool = False) -> str:
    """Port of upstream get_anscheck_prompt, including its NotImplementedError."""
    if abstention:
        return _ABSTENTION.format(question, answer, response)
    if task in ("single-session-user", "single-session-assistant", "multi-session"):
        return _GENERIC.format(question, answer, response)
    if task == "temporal-reasoning":
        return _TEMPORAL.format(question, answer, response)
    if task == "knowledge-update":
        return _KNOWLEDGE_UPDATE.format(question, answer, response)
    if task == "single-session-preference":
        return _PREFERENCE.format(question, answer, response)
    raise NotImplementedError(f"unsupported question type: {task}")


def label_from_reply(raw: str) -> bool:
    """Upstream's label rule: `'yes' in response.lower()`."""
    return "yes" in (raw or "").lower()


@dataclass
class Verdict:
    correct: bool
    raw: str
    question_type: str
    abstention: bool
    prompt: str = ""


async def score_async(question_id: str, question_type: str, question: str,
                      gold: str, response: str, call_llm) -> Verdict:
    """Judge one answer. `call_llm(prompt, system, max_tokens) -> str`.

    Raises EmptyJudgeReply when the judge says nothing at all.
    """
    abst = is_abstention(question_id)
    prompt = get_anscheck_prompt(question_type, question, gold, response,
                                 abstention=abst)
    # Upstream sends the prompt as a bare user message with no system prompt,
    # temperature 0, max_tokens 10.
    raw = (await call_llm(prompt, "", 10) or "").strip()
    if not raw:
        raise EmptyJudgeReply(
            "judge returned an empty reply (max_tokens=10 can be consumed "
            "by a reasoning model's hidden reasoning); use a non-reasoning "
            "judge such as gpt-4o")
    return Verdict(correct=label_from_reply(raw), raw=raw,
                   question_type=question_type, abstention=abst, prompt=prompt)


def is_scored(row: Dict[str, Any]) -> bool:
    """A row counts only if it finished without error and the judge answered."""
    return not row.get("error") and bool((row.get("judge_raw") or "").strip())


def is_correct(row: Dict[str, Any]) -> bool:
    return is_scored(row) and label_from_reply(row["judge_raw"])


def _acc(correct: int, n: int):
    return round(correct / n, 4) if n else None


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Headline accuracy over the ANSWERABLE questions, plus per-type.

    Rows with an error, or without a judge reply, are excluded from every
    figure and counted under `errored`. They are never scored as wrong.

    The headline excludes the 30 abstention questions. They ask whether the
    model correctly refused to answer, are graded against a different prompt
    that tests refusal rather than recall, and measure something the rest of
    the benchmark does not. Averaging the two together produces a number that
    is neither. They are still run and still scored, reported in full under
    `abstention`, they just do not enter the headline.

    `n`, `correct` and `accuracy` therefore describe the answerable set.
    `all_questions` keeps the combined figure for anyone who wants it.
    """
    scored = [r for r in rows if is_scored(r)]
    abst = [r for r in scored if r.get("abstention")]
    ans = [r for r in scored if not r.get("abstention")]

    total = len(ans)
    correct = sum(1 for r in ans if is_correct(r))

    # Per-type breakdown follows the headline and covers answerable only, so
    # the type rows sum to the headline instead of quietly exceeding it.
    by_type: Dict[str, Dict[str, Any]] = {}
    for r in ans:
        t = r.get("question_type") or "unknown"
        b = by_type.setdefault(t, {"n": 0, "correct": 0})
        b["n"] += 1
        b["correct"] += 1 if is_correct(r) else 0
    for b in by_type.values():
        b["accuracy"] = round(b["correct"] / b["n"], 4) if b["n"] else 0.0

    all_correct = sum(1 for r in scored if is_correct(r))
    abst_correct = sum(1 for r in abst if is_correct(r))
    return {
        "n": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "scope": "answerable questions only; abstention excluded",
        "errored": len(rows) - len(scored),
        "all_questions": {
            "n": len(scored),
            "correct": all_correct,
            "accuracy": _acc(all_correct, len(scored)),
        },
        "by_question_type": dict(sorted(by_type.items())),
        "abstention": {
            "n": len(abst),
            "correct": abst_correct,
            "accuracy": _acc(abst_correct, len(abst)),
        },
        "answerable": {
            "n": len(ans),
            "correct": correct,
            "accuracy": _acc(correct, len(ans)),
        },
    }
