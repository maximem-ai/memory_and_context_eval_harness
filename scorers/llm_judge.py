import logging
import os
import re
from typing import List, Dict

logger = logging.getLogger(__name__)

_DEFAULT_JUDGE_PROMPT = """You are an evaluation judge. Compare the predicted answer against the gold (expected) answer for the given question.

Question: {question}
Gold Answer: {gold}
Predicted Answer: {prediction}

Score the prediction from 0.0 to 1.0 based on whether the KEY FACTUAL CONTENT of the gold answer is present ANYWHERE in the prediction — including in the reasoning, listed facts, key findings, or analysis sections, not just the final conclusion:
- 1.0 = the gold answer's key facts appear anywhere in the prediction, even if the model also mentions other facts or picks a different final answer
- 0.7-0.9 = the gold answer is partially referenced or paraphrased in the prediction
- 0.4-0.6 = the prediction touches on the topic but the gold answer's key facts are vague or incomplete
- 0.1-0.3 = the prediction is about the right topic but does not contain the gold answer's key facts
- 0.0 = the prediction is completely wrong, irrelevant, or says "I don't know" and the gold answer is never mentioned

IMPORTANT:
- The prediction may list multiple facts or memories before giving a final answer. If the gold answer appears in ANY of those listed facts, score 1.0 — the system successfully retrieved and recognized the correct information.
- Do NOT penalize for extra information, verbose answers, or choosing a different final answer when the correct one is also present.
- If the gold answer lists alternate acceptable values (e.g. "X or Y"), the prediction is fully correct if it contains ANY of the accepted values.

Respond with ONLY a compact single-line JSON object (no newlines):
{{"score": <float>, "reason": "<brief explanation>"}}"""

_TEMPORAL_JUDGE_PROMPT = """You are an evaluation judge for temporal reasoning questions.

Question: {question}
Gold Answer: {gold}
Predicted Answer: {prediction}

Score the prediction from 0.0 to 1.0. Be LENIENT with:
- Off-by-one day errors (e.g. "Monday" vs "Tuesday" for adjacent events)
- Equivalent date expressions ("last week" vs "7 days ago", "in the morning" vs "around 9am")
- Approximate time ranges that include the correct time
- Different but valid date formats

Only penalize when the prediction is clearly wrong about the time or sequence of events.

Respond with ONLY a compact single-line JSON object (no newlines):
{{"score": <float>, "reason": "<brief explanation>"}}"""

_KNOWLEDGE_UPDATE_JUDGE_PROMPT = """You are an evaluation judge for knowledge-update questions. The user may have provided updated information that supersedes earlier information.

Question: {question}
Gold Answer: {gold}
Predicted Answer: {prediction}

Score the prediction from 0.0 to 1.0:
- 1.0 = prediction contains the MOST RECENT / UPDATED information from the gold answer (even if it also mentions the older information)
- 0.5 = prediction only contains the outdated information (did not track the update)
- 0.0 = prediction is entirely wrong or irrelevant

IMPORTANT: If the gold answer reflects an update, the prediction MUST contain the updated value to score above 0.5. Mentioning only the old value is incorrect.

Respond with ONLY a compact single-line JSON object (no newlines):
{{"score": <float>, "reason": "<brief explanation>"}}"""

_PREFERENCE_JUDGE_PROMPT = """You are an evaluation judge for preference and persona questions. I will give you a question, a rubric for the desired personalized response, and a system's hypothesis.

Question: {question}
Rubric: {gold}
System's Hypothesis: {prediction}

The model does not need to reflect all the points in the rubric. Score based on whether the hypothesis recalls and utilizes the user's personal information correctly.

Score the prediction from 0.0 to 1.0:
- 1.0 = hypothesis correctly applies or describes the user's preference (exact wording not required)
- 0.7-0.9 = hypothesis mostly reflects the preference with minor gaps
- 0.4-0.6 = hypothesis partially captures the preference
- 0.1-0.3 = hypothesis mentions the topic but mischaracterizes the preference
- 0.0 = hypothesis is wrong, ignores the preference, or hallucinates a different preference

Respond with ONLY a compact single-line JSON object (no newlines):
{{"score": <float>, "reason": "<brief explanation>"}}"""

_ADVERSARIAL_JUDGE_PROMPT = """You are an evaluation judge for adversarial / unanswerable questions. The correct behavior is to REFUSE to answer or state that the information is not available.

Question: {question}
Gold Answer: {gold}
Predicted Answer: {prediction}

Score the prediction from 0.0 to 1.0:
- 1.0 = prediction correctly declines to answer, or states the information is not available / cannot be determined
- 0.5 = prediction expresses uncertainty but still partially guesses
- 0.0 = prediction confidently provides a specific answer (hallucination)

IMPORTANT: Any confident specific answer to an unanswerable question scores 0.0, even if it happens to be plausible.

Respond with ONLY a compact single-line JSON object (no newlines):
{{"score": <float>, "reason": "<brief explanation>"}}"""

# Maps question category strings → prompt template
_QUESTION_TYPE_TO_PROMPT = {
    # temporal
    "temporal": _TEMPORAL_JUDGE_PROMPT,
    "temporal-reasoning": _TEMPORAL_JUDGE_PROMPT,
    "temporal_reasoning": _TEMPORAL_JUDGE_PROMPT,
    # knowledge update
    "knowledge-update": _KNOWLEDGE_UPDATE_JUDGE_PROMPT,
    "knowledge_update": _KNOWLEDGE_UPDATE_JUDGE_PROMPT,
    "changing_evidence": _KNOWLEDGE_UPDATE_JUDGE_PROMPT,
    # preference
    "single-session-preference": _PREFERENCE_JUDGE_PROMPT,
    "preference": _PREFERENCE_JUDGE_PROMPT,
    "preference_evidence": _PREFERENCE_JUDGE_PROMPT,
    # adversarial / abstention
    "adversarial": _ADVERSARIAL_JUDGE_PROMPT,
    "abstention_evidence": _ADVERSARIAL_JUDGE_PROMPT,
}

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
_JUDGE_PROMPT_PATH = os.path.join(_PROMPTS_DIR, "judge.md")
_TEMPORAL_JUDGE_PROMPT_PATH = os.path.join(_PROMPTS_DIR, "judge_temporal.md")
_judge_prompt_cache: str = None
_temporal_judge_prompt_cache: str = None


def _get_judge_prompt() -> str:
    """Load judge prompt from prompts/judge.md if it exists, otherwise use default."""
    global _judge_prompt_cache
    if _judge_prompt_cache is not None:
        return _judge_prompt_cache

    if os.path.exists(_JUDGE_PROMPT_PATH):
        with open(_JUDGE_PROMPT_PATH, "r") as f:
            _judge_prompt_cache = f.read().strip()
        logger.info("Loaded judge prompt from %s", _JUDGE_PROMPT_PATH)
    else:
        _judge_prompt_cache = _DEFAULT_JUDGE_PROMPT
    return _judge_prompt_cache


def _get_temporal_judge_prompt() -> str:
    """Load temporal judge prompt from prompts/judge_temporal.md if it exists, otherwise use the hardcoded one."""
    global _temporal_judge_prompt_cache
    if _temporal_judge_prompt_cache is not None:
        return _temporal_judge_prompt_cache

    if os.path.exists(_TEMPORAL_JUDGE_PROMPT_PATH):
        with open(_TEMPORAL_JUDGE_PROMPT_PATH, "r") as f:
            _temporal_judge_prompt_cache = f.read().strip()
        logger.info("Loaded temporal judge prompt from %s", _TEMPORAL_JUDGE_PROMPT_PATH)
    else:
        _temporal_judge_prompt_cache = _TEMPORAL_JUDGE_PROMPT
    return _temporal_judge_prompt_cache


def _is_reasoning_model(model: str) -> bool:
    """Detect OpenAI reasoning models that don't support temperature or max_tokens.

    Reasoning models: o1, o3, o4-mini, gpt-5-mini, and future o-series.
    Chat models: gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-3.5-turbo — support all params.
    """
    m = model.lower()
    # o-series reasoning models (o1, o3, o4-mini, etc.)
    if re.match(r'^o[0-9]', m):
        return True
    # gpt-5-mini and future gpt-5 variants are reasoning models
    if 'gpt-5' in m:
        return True
    return False


async def _llm_call(model: str, prompt: str, retries: int = 3, max_output_tokens: int = 2048) -> str:
    """Call LLM via direct HTTP — no SDK wrapping. Retries up to `retries` times with backoff."""
    import asyncio, os, httpx
    last_exc = None
    for attempt in range(retries):
        try:
            if model.startswith("gemini"):
                api_key = os.environ.get("GEMINI_API_KEY", "")
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": max_output_tokens},
                }
                async with httpx.AsyncClient(timeout=90) as client:
                    r = await client.post(url, json=payload)
                    r.raise_for_status()
                    return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            else:
                import openai
                client = openai.AsyncOpenAI()
                kwargs = dict(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_completion_tokens=max_output_tokens,
                )
                if not _is_reasoning_model(model):
                    kwargs["temperature"] = 0.0
                resp = await client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content or ""
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(f"Judge LLM call failed (attempt {attempt + 1}/{retries}), retrying in {wait}s: {e}")
                await asyncio.sleep(wait)
    raise last_exc


def _parse_score(text: str) -> Dict:
    """Extract score from LLM response — handles partial/malformed JSON."""
    if not text:
        return {"score": 0.0, "reason": "Empty response"}

    # Strip markdown fences
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text).rstrip("`").strip()

    # Primary: regex extract score directly — works even on truncated JSON
    score_match = re.search(r'"score"\s*:\s*([0-9]*\.?[0-9]+)', text)
    reason_match = re.search(r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"', text)

    if score_match:
        return {
            "score": float(score_match.group(1)),
            "reason": reason_match.group(1) if reason_match else "",
        }

    # Fallback: try full JSON parse
    try:
        import json
        # try to find a {...} block
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            result = json.loads(m.group(0))
            return {"score": float(result.get("score", 0.0)), "reason": result.get("reason", "")}
    except Exception:
        pass

    logger.warning(f"Judge could not parse response: {text!r}")
    return {"score": 0.0, "reason": f"Parse failed: {text[:80]}"}


_ABSTAIN_GOLD_VALUES = {
    "", "unanswerable", "not mentioned", "not specified",
    "not in the conversation", "no information", "unknown",
}


def _select_judge_prompt(question_type: str, gold: str = "") -> str:
    """Return the appropriate judge prompt template for the given question type.

    Special case: if the question is labeled adversarial/abstention but the gold
    answer is concrete (e.g. binary "Yes"/"No"), use the default content-match
    judge instead of the adversarial-abstain judge — the adversarial judge
    incorrectly scores correct concrete answers as 0.0 because it requires
    explicit refusal.
    """
    if question_type:
        qt = question_type.lower().strip()
        if qt in ("adversarial", "abstention_evidence"):
            gold_norm = (gold or "").strip().lower()
            if gold_norm and gold_norm not in _ABSTAIN_GOLD_VALUES:
                # Concrete gold (e.g. "No") — fall through to default judge
                return _get_judge_prompt()
        if qt in ("temporal", "temporal-reasoning", "temporal_reasoning"):
            return _get_temporal_judge_prompt()
        if qt in _QUESTION_TYPE_TO_PROMPT:
            return _QUESTION_TYPE_TO_PROMPT[qt]
    return _get_judge_prompt()


async def judge_single(
    question: str,
    prediction: str,
    gold: str,
    model: str = "gpt-4o",
    question_type: str = "",
) -> Dict:
    """Judge a single prediction against gold answer using LLM.

    Selects a question-type-specific prompt when available (temporal,
    knowledge-update, preference, adversarial); falls back to the default.
    """
    try:
        prompt_template = _select_judge_prompt(question_type, gold)
        text = await _llm_call(
            model,
            prompt_template.format(question=question, gold=gold, prediction=prediction),
        )
        return _parse_score(text)
    except Exception as e:
        logger.error(f"LLM judge failed: {e}")
        return {"score": 0.0, "reason": f"Judge error: {e}"}


RELEVANCE_PROMPT = """You are a relevance judge. Given a user message and the context retrieved by a memory system, score how relevant the retrieved context is to the user's message.

User Message: {query}
Retrieved Context: {context}

Score from 0.0 to 1.0:
- 1.0 = context directly addresses the user's message with specific, useful information
- 0.7-0.9 = context is mostly relevant, contains useful background
- 0.4-0.6 = partially relevant, some useful info mixed with irrelevant
- 0.1-0.3 = mostly irrelevant but tangentially related
- 0.0 = completely irrelevant or empty

Respond with ONLY a compact single-line JSON object (no newlines):
{{"score": <float>, "reason": "<brief explanation>"}}"""


def _compact_context(context: str, max_chars: int = 6000) -> str:
    if len(context) <= max_chars:
        return context
    items = re.split(r'\n---\n|\n\n|\n(?=\S)', context)
    if len(items) <= 1:
        return context[:max_chars]
    per_item = max(100, (max_chars - len(items) * 5) // len(items))
    compacted = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        compacted.append(item[:per_item] + "…" if len(item) > per_item else item)
    return "\n---\n".join(compacted)


async def judge_relevance(query: str, context: str, model: str = "gpt-4o") -> Dict:
    """Judge how relevant retrieved context is to the user's query."""
    if not context.strip():
        return {"score": 0.0, "reason": "Empty context"}
    try:
        text = await _llm_call(
            model,
            RELEVANCE_PROMPT.format(query=query, context=_compact_context(context)),
        )
        return _parse_score(text)
    except Exception as e:
        logger.error(f"LLM relevance judge failed: {e}")
        return {"score": 0.0, "reason": f"Judge error: {e}"}


class LLMJudge:
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.model = self.config.get("model", "gpt-4o")

    async def score_single(self, question: str, prediction: str, gold: str) -> Dict:
        return await judge_single(question, prediction, gold, self.model)

    def score(self, predictions: List[Dict]) -> float:
        import asyncio
        async def _batch():
            scores = []
            for p in predictions:
                result = await judge_single(
                    p.get("question", ""),
                    p.get("prediction", ""),
                    p.get("gold_answer", ""),
                    self.model,
                )
                scores.append(result["score"])
            return sum(scores) / len(scores) if scores else 0.0
        return asyncio.run(_batch())


# ── Retrieval Quality Evaluation (Layer 1) ────────────────────────────────────

_RETRIEVAL_EVAL_PROMPT = """You are evaluating search results for relevance to a question.

QUESTION:
{question}

EXPECTED ANSWER:
{ground_truth}

SEARCH RESULTS:
{formatted_results}

TASK:
For each search result, evaluate TWO things:
1. "relevant": Does this search result contain information relevant to answering the question?
   A result is relevant if it contains content that directly answers the question, or provides
   a specific fact that supports or leads to the expected answer. (1=yes, 0=no)
2. "evidence_hit": If the result has a source_evidence field, does that raw conversation text
   contain additional details relevant to the answer beyond what the memory field captures?
   (1=yes, 0=no. If no source_evidence field, set to 0)

Return a JSON array with your evaluation for each result:
[
  {{"id": "result_1", "relevant": 1, "evidence_hit": 0}},
  {{"id": "result_2", "relevant": 0, "evidence_hit": 1}},
  ...
]

Return ONLY the JSON array, no other text."""


_RETRIEVAL_EVAL_PROMPT_PREFERENCE = """You are evaluating search results for a preference or recommendation question.

QUESTION:
{question}

WHAT A CORRECT ANSWER LOOKS LIKE:
{ground_truth}

SEARCH RESULTS:
{formatted_results}

TASK:
For each search result, evaluate TWO things:
1. "relevant": Is this search result useful for answering the question? (1=yes, 0=no)
   An item is useful if it reveals the user's equipment, preferences, interests, goals, situation,
   or background in the domain of the question. It does NOT need to directly state the answer.
   Mark 0 ONLY if it is completely unrelated to the question domain.
2. "evidence_hit": If the result has a source_evidence field, does that raw conversation text
   contain additional details useful for this question beyond what the memory field captures?
   (1=yes, 0=no. If no source_evidence field, set to 0)

Return a JSON array with your evaluation for each result:
[
  {{"id": "result_1", "relevant": 1, "evidence_hit": 0}},
  {{"id": "result_2", "relevant": 0, "evidence_hit": 1}},
  ...
]

Return ONLY the JSON array, no other text."""


_RETRIEVAL_EVAL_PROMPT_MULTI_SESSION = """You are evaluating search results for a multi-session memory question.

QUESTION:
{question}

EXPECTED ANSWER:
{ground_truth}

SEARCH RESULTS:
{formatted_results}

TASK:
For each search result, evaluate TWO things:
1. "relevant": Does this search result contribute to answering the question? (1=yes, 0=no)
   An item contributes if it provides any evidence, context, or information that — alone or
   combined with other items — helps build toward the expected answer. Mark 0 only if entirely
   unrelated to the topic of the question.
2. "evidence_hit": If the result has a source_evidence field, does that raw conversation text
   contain additional details that contribute to the answer beyond what the memory field captures?
   (1=yes, 0=no. If no source_evidence field, set to 0)

Return a JSON array with your evaluation for each result:
[
  {{"id": "result_1", "relevant": 1, "evidence_hit": 0}},
  {{"id": "result_2", "relevant": 0, "evidence_hit": 1}},
  ...
]

Return ONLY the JSON array, no other text."""


# Maps question type → retrieval eval prompt
_RETRIEVAL_PROMPT_BY_TYPE = {
    "single-session-preference": _RETRIEVAL_EVAL_PROMPT_PREFERENCE,
    "preference": _RETRIEVAL_EVAL_PROMPT_PREFERENCE,
    "preference_evidence": _RETRIEVAL_EVAL_PROMPT_PREFERENCE,
    "multi-session": _RETRIEVAL_EVAL_PROMPT_MULTI_SESSION,
    "multi_session": _RETRIEVAL_EVAL_PROMPT_MULTI_SESSION,
}


def _compute_retrieval_metrics(
    memory_relevance: List[int],
    evidence_relevance: List[int],
    k: int,
    chunk_evidence_hit: bool = False,
) -> Dict:
    """Compute retrieval metrics from two sets of binary relevance flags.

    - memory_relevance: flags based on the extracted memory summary only
    - evidence_relevance: flags based on source_evidence (raw conversation)

    Hit@K and Recall@K use COMBINED relevance (memory OR evidence) — because
    the retrieval system DID surface the raw data even if extraction missed it.

    Precision@K, F1@K, MRR, NDCG use MEMORY-ONLY relevance — because these
    measure the quality of the extracted/summarized items themselves.

    Matches MemoryBench's computation for the memory-only metrics.
    """
    import math

    if not memory_relevance:
        return {"hit_at_k": 0.0, "precision_at_k": 0.0, "recall_at_k": 0.0,
                "f1_at_k": 0.0, "mrr": 0.0, "ndcg": 0.0, "k": k,
                "relevant_retrieved": 0, "total_relevant": 1}

    mem = memory_relevance[:k]
    evi = (evidence_relevance or [0] * len(mem))[:k]
    combined = [1 if (m or e) else 0 for m, e in zip(mem, evi)]

    # Memory-only counts (for precision, f1, mrr, ndcg)
    mem_relevant = sum(mem)
    total_relevant = max(1, mem_relevant)

    # Combined counts (for hit, recall) — includes chunk evidence
    combined_relevant = sum(combined)
    any_relevant = combined_relevant > 0 or chunk_evidence_hit

    # Hit@K and Recall@K — use combined (memory OR item evidence OR chunk evidence)
    hit_at_k = 1.0 if any_relevant else 0.0
    recall_at_k = 1.0 if any_relevant else 0.0

    # Precision@K, F1@K, MRR, NDCG — use memory-only
    precision_at_k = round(mem_relevant / len(mem), 4) if mem else 0.0
    f1_at_k = round(
        2 * precision_at_k * recall_at_k / (precision_at_k + recall_at_k), 4
    ) if (precision_at_k + recall_at_k) > 0 else 0.0

    first_relevant = next((i for i, r in enumerate(mem) if r == 1), -1)
    mrr = round(1.0 / (first_relevant + 1), 4) if first_relevant >= 0 else 0.0

    # NDCG: MemoryBench places totalRelevant ones at the top as the ideal
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(mem))
    ideal_scores = [1 if i < min(total_relevant, len(mem)) else 0 for i in range(len(mem))]
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal_scores))
    ndcg = round(dcg / idcg, 4) if idcg > 0 else 0.0

    return {
        "hit_at_k": hit_at_k,
        "precision_at_k": precision_at_k,
        "recall_at_k": recall_at_k,
        "f1_at_k": f1_at_k,
        "mrr": mrr,
        "ndcg": ndcg,
        "k": len(mem),
        "relevant_retrieved": mem_relevant,
        "evidence_relevant": sum(1 for m, e in zip(mem, evi) if e and not m),
        "total_relevant": total_relevant,
    }


def _parse_relevance_labels(text: str, expected_count: int) -> Dict[str, List[int]]:
    """Parse retrieval judge response with two flags per item.

    Handles multiple id formats:
    - "result_1", "result_2" … (our prompt's format)
    - Actual UUIDs (judge sometimes echoes item ids)
    - Any other format → fall back to positional (array order = item order)

    Returns {"memory": [...], "evidence": [...]} — two parallel lists of 0/1 flags.
    """
    import json
    zeros = {"memory": [0] * expected_count, "evidence": [0] * expected_count}
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text).rstrip("`").strip()

    m = re.search(r'\[[\s\S]*\]', text)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if parsed and isinstance(parsed[0], dict):
                memory_labels = [0] * expected_count
                evidence_labels = [0] * expected_count

                # First try: match by "result_N" pattern in id field
                matched_by_id = False
                if "id" in parsed[0]:
                    for item in parsed:
                        id_str = str(item.get("id", ""))
                        id_match = re.search(r'^result_(\d+)$', id_str)
                        if id_match:
                            idx = int(id_match.group(1)) - 1  # 1-indexed → 0-indexed
                            if 0 <= idx < expected_count:
                                memory_labels[idx] = 1 if item.get("relevant") == 1 else 0
                                evidence_labels[idx] = 1 if item.get("evidence_hit") == 1 else 0
                                matched_by_id = True

                # Fallback: positional — use array order (handles UUIDs, any other id format)
                if not matched_by_id:
                    for idx, item in enumerate(parsed):
                        if idx < expected_count:
                            memory_labels[idx] = 1 if item.get("relevant") == 1 else 0
                            evidence_labels[idx] = 1 if item.get("evidence_hit") == 1 else 0

                return {"memory": memory_labels, "evidence": evidence_labels}
            # Fallback: plain int array (legacy, no evidence)
            if parsed and isinstance(parsed[0], (int, float)):
                return {
                    "memory": [1 if v else 0 for v in parsed[:expected_count]],
                    "evidence": [0] * expected_count,
                }
        except Exception:
            pass

    logger.warning(f"Could not parse retrieval relevance labels from: {text!r}")
    return zeros


async def judge_retrieval_quality(
    question: str,
    gold: str,
    item_objects: List[Dict],
    model: str = "gpt-4o",
    question_type: str = "",
    evidence_chunks: List[str] = None,
) -> Dict:
    """Judge retrieval quality for a single question.

    Selects a question-type-aware retrieval eval prompt:
    - preference / single-session-preference → contextual usefulness criterion
    - multi-session → contribution-to-synthesis criterion
    - all others → direct factual relevance (MemoryBench default)

    Returns two sets of relevance flags:
    - memory relevance: used for Precision@K, F1@K, MRR, NDCG
    - evidence relevance (source_evidence + evidence_chunks): combined with memory for Hit@K, Recall@K
    """
    import json
    k = len(item_objects)
    if k == 0:
        logger.warning("judge_retrieval_quality called with 0 items — returning zeros")
        return {**_compute_retrieval_metrics([], [], 0), "relevance_flags": [], "evidence_flags": [], "chunk_evidence_hit": False, "error": "no_items"}

    # Select prompt based on question type
    qt = (question_type or "").lower().strip()
    prompt_template = _RETRIEVAL_PROMPT_BY_TYPE.get(qt, _RETRIEVAL_EVAL_PROMPT)

    formatted_results = "\n\n".join(
        f"=== result_{i + 1} ===\n{json.dumps(obj, indent=2, default=str)}"
        for i, obj in enumerate(item_objects)
    )
    prompt = prompt_template.format(
        question=question,
        ground_truth=gold,
        formatted_results=formatted_results,
    )

    try:
        text = await _llm_call(model, prompt, max_output_tokens=8192)
        logger.debug(f"Retrieval judge raw response ({qt or 'default'}): {text!r}")
        labels = _parse_relevance_labels(text, k)
        memory_flags = labels["memory"]
        evidence_flags = labels["evidence"]
        if all(r == 0 for r in memory_flags) and all(r == 0 for r in evidence_flags):
            logger.warning(
                f"Retrieval judge returned all-zero for both memory and evidence ({k} items, "
                f"question_type={qt!r}). Raw: {text!r}"
            )

        # Evaluate bundle-level evidence_chunks for Hit/Recall (not ranked, so no Precision/MRR/NDCG)
        chunk_hit = False
        if evidence_chunks:
            chunk_prompt = (
                f"Question: {question}\nExpected Answer: {gold}\n\n"
                f"The following are raw conversation excerpts retrieved by the memory system:\n\n"
                + "\n\n".join(f"--- chunk {i+1} ---\n{c}" for i, c in enumerate(evidence_chunks))
                + "\n\nDo any of these excerpts contain information that helps answer the question? "
                  "Respond with ONLY: {\"relevant\": 1} or {\"relevant\": 0}"
            )
            try:
                chunk_resp = await _llm_call(model, chunk_prompt, max_output_tokens=64)
                chunk_resp_lower = chunk_resp.lower().strip()
                chunk_hit = (
                    '"relevant": 1' in chunk_resp_lower
                    or '"relevant":1' in chunk_resp_lower
                    or '"relevant": true' in chunk_resp_lower
                    or '"relevant":true' in chunk_resp_lower
                    or 'yes' in chunk_resp_lower
                )
                logger.debug(f"Chunk evidence judge: hit={chunk_hit}, raw={chunk_resp!r}")
            except Exception as ce:
                logger.warning(f"Chunk evidence judge failed: {ce}")

        metrics = _compute_retrieval_metrics(memory_flags, evidence_flags, k, chunk_evidence_hit=chunk_hit)
        metrics["relevance_flags"] = memory_flags
        metrics["evidence_flags"] = evidence_flags
        metrics["chunk_evidence_hit"] = chunk_hit
        metrics["error"] = None
        return metrics
    except Exception as e:
        logger.error(f"Retrieval quality judge failed ({type(e).__name__}): {e}")
        return {**_compute_retrieval_metrics([], [], k), "relevance_flags": [], "evidence_flags": [], "chunk_evidence_hit": False, "error": str(e)}
