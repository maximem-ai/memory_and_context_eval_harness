"""
Answer Phase — generate hypotheses for each question using an LLM.

Reads search results from disk, builds a prompt with context, and calls
the configured answering model.
"""

import logging
import os
import re
import time
from typing import Any, Callable, Dict, Optional

from runner.types import (
    Benchmark,
    Provider,
    RunCheckpoint,
    build_context_string,
    resolve_concurrency,
)
from runner.checkpoint import CheckpointManager
from runner.concurrent import execute_concurrent

logger = logging.getLogger(__name__)

PROMPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "prompts",
)
DEFAULT_SYSTEM_PROMPT_PATH = os.path.join(PROMPTS_DIR, "qa_agent.md")
LEGACY_DEFAULT_SYSTEM_PROMPT_PATH = os.path.join(PROMPTS_DIR, "locomo_agent.md")

DEFAULT_ANSWER_TEMPLATE = """Question: {question}
Question Date: {question_date}

Retrieved Context (raw JSON from memory provider):
{context}

Answer:"""


_SUFFIX_BLOCK = re.compile(r"<!-- suffix:begin -->.*?<!-- suffix:end -->", re.S)


def _render_system_prompt(text: str) -> str:
    """Turn a prompt template into plain system-prompt text.

    A template may carry a `{question}`/`{context}` suffix block and chunk
    markers for the standalone runner. Here the question and context are sent
    in the user message, so the suffix is dropped and the chunk markers are
    resolved with source chunks included.
    """
    from benchmarks.common import apply_chunk_mode

    text = _SUFFIX_BLOCK.sub("", text)
    return apply_chunk_mode(text, include_chunks=True)[0].strip()


def _load_default_system_prompt(benchmark_name: str = "") -> str:
    """Load the answer system prompt for the given benchmark.

    Lookup order:
    1. prompts/{benchmark_name}_agent.md  — per-benchmark prompt if it exists
    2. prompts/qa_agent.md                — shared default
    3. prompts/locomo_agent.md            — legacy fallback (pre-rename)
    4. hardcoded one-line fallback
    """
    candidates = []
    if benchmark_name:
        candidates.append(os.path.join(PROMPTS_DIR, f"{benchmark_name}_agent.md"))
    candidates += [DEFAULT_SYSTEM_PROMPT_PATH, LEGACY_DEFAULT_SYSTEM_PROMPT_PATH]

    for path in candidates:
        if os.path.exists(path):
            with open(path, "r") as f:
                return _render_system_prompt(f.read())
    logger.warning("No default system prompt found in %s", PROMPTS_DIR)
    return "You are a helpful assistant with access to conversation memory."


def _use_longmemeval_prompt(benchmark: Benchmark, system_prompt: str) -> bool:
    """True when this is a LongMemEval run on the shipped, unmodified prompt."""
    return (getattr(benchmark, "name", "") == "longmemeval"
            and system_prompt == _load_default_system_prompt("longmemeval"))


def _build_longmemeval_prompt(question: str, results: list, question_date: Optional[str]) -> str:
    """Build the answer message exactly as the standalone LongMemEval-S runner does.

    One user message: the prompt, a current-date line, then the question and
    the retrieved memories rendered as text (with source chunks and metadata),
    not as raw JSON.
    """
    from benchmarks.common import apply_chunk_mode
    from benchmarks.longmemeval.run import build_answer_prompt

    path = os.path.join(PROMPTS_DIR, "longmemeval_agent.md")
    if not os.path.exists(path):
        path = DEFAULT_SYSTEM_PROMPT_PATH
    with open(path, "r") as f:
        template, _ = apply_chunk_mode(f.read(), True)
    return build_answer_prompt(template, question, results, question_date,
                               include_chunks=True, include_metadata=True)


async def answer_one(
    question_id: str,
    benchmark: Benchmark,
    checkpoint: RunCheckpoint,
    checkpoint_mgr: CheckpointManager,
    answering_model: str,
    system_prompt: str = "",
    provider: Optional[Provider] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Generate the hypothesis for a single question and persist it."""
    qcp = checkpoint.questions[question_id]
    t0 = time.monotonic()

    checkpoint_mgr.update_answer_phase(
        checkpoint, question_id, status="in_progress",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    try:
        search_data = checkpoint_mgr.load_search_results(checkpoint.run_id, question_id)
        results = search_data.get("results", []) if search_data else []

        if _use_longmemeval_prompt(benchmark, system_prompt):
            prompt = _build_longmemeval_prompt(qcp.question, results, qcp.question_date)
            system_prompt = ""
        else:
            prompt = _build_answer_prompt(
                qcp.question,
                results,
                qcp.question_date,
                provider,
                system_prompt,
            )

        hypothesis = await _call_llm(prompt, answering_model, system_prompt)
        duration_ms = round((time.monotonic() - t0) * 1000, 1)

        checkpoint_mgr.update_answer_phase(
            checkpoint, question_id,
            status="completed",
            hypothesis=hypothesis,
            duration_ms=duration_ms,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        await checkpoint_mgr.save(checkpoint)

        if on_progress:
            on_progress({
                "type": "answer_complete",
                "question_id": question_id,
                "duration_ms": duration_ms,
            })

        return {"question_id": question_id, "duration_ms": duration_ms}

    except Exception as e:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        checkpoint_mgr.update_answer_phase(
            checkpoint, question_id,
            status="failed",
            error=str(e),
            duration_ms=duration_ms,
        )
        await checkpoint_mgr.save(checkpoint)
        logger.error("[answer] Question %s failed: %s", question_id, e)
        return {"question_id": question_id, "error": str(e)}


async def run_answer_phase(
    benchmark: Benchmark,
    checkpoint: RunCheckpoint,
    checkpoint_mgr: CheckpointManager,
    answering_model: str,
    system_prompt: str = "",
    provider: Optional[Provider] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Run the answer phase for all questions with completed search (serial-phase mode)."""
    if not system_prompt:
        system_prompt = _load_default_system_prompt(getattr(benchmark, "name", ""))

    pending = [
        qid for qid, qcp in checkpoint.questions.items()
        if (
            checkpoint_mgr.get_phase_status(checkpoint, qid, "search") == "completed"
            and checkpoint_mgr.get_phase_status(checkpoint, qid, "answer") != "completed"
        )
    ]

    if not pending:
        logger.info("[answer] All questions already answered")
        return

    concurrency = resolve_concurrency(
        "answer",
        checkpoint.concurrency,
        provider.concurrency if provider else None,
    )
    logger.info("[answer] Answering %d questions (model=%s, concurrency=%d)", len(pending), answering_model, concurrency)

    async def _wrap(question_id: str, index: int) -> Dict[str, Any]:
        return await answer_one(
            question_id, benchmark, checkpoint, checkpoint_mgr,
            answering_model, system_prompt, provider, on_progress,
        )

    await execute_concurrent(pending, concurrency, "answer", _wrap)


def _build_answer_prompt(
    question: str,
    search_results: list,
    question_date: Optional[str],
    provider: Optional[Provider],
    system_prompt: str,
) -> str:
    """Build the answer prompt, using provider custom prompt if available."""
    if provider and provider.prompts and provider.prompts.answer_prompt:
        ap = provider.prompts.answer_prompt
        if callable(ap):
            return ap(question, search_results, question_date)
        context_str = build_context_string(search_results)
        return ap.replace("{question}", question).replace(
            "{context}", context_str
        ).replace("{question_date}", question_date or "N/A")

    context_str = build_context_string(search_results)
    return DEFAULT_ANSWER_TEMPLATE.format(
        question=question,
        context=context_str,
        question_date=question_date or "N/A",
    )


def _is_reasoning_model(model: str) -> bool:
    """Check if a model is a reasoning model (skip temperature param)."""
    reasoning_prefixes = ("o1", "o3", "o4", "gpt-5")
    return any(model.startswith(p) or model.startswith(f"openai/{p}") for p in reasoning_prefixes)


async def _call_llm(user_message: str, model: str, system_prompt: str = "") -> str:
    """Call an LLM to generate an answer."""
    try:
        if model.startswith("gemini"):
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import HumanMessage, SystemMessage

            gemini_key = (
                os.environ.get("GEMINI_API_KEY")
                or os.environ.get("Gemini_API_Key")
                or os.environ.get("GOOGLE_API_KEY", "")
            )
            os.environ["GEMINI_API_KEY"] = gemini_key

            llm = ChatGoogleGenerativeAI(
                model=model,
                temperature=0.0,
                max_output_tokens=4096,
            )
            messages = []
            if system_prompt:
                messages.append(SystemMessage(content=system_prompt))
            messages.append(HumanMessage(content=user_message))
            response = await llm.ainvoke(messages)
            content = response.content
            if isinstance(content, list):
                content = " ".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
            return content or ""
        else:
            import openai
            client = openai.AsyncOpenAI()
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": user_message})
            kwargs = dict(
                model=model,
                messages=messages,
                max_completion_tokens=4096,
            )
            if not _is_reasoning_model(model):
                kwargs["temperature"] = 0.0
            response = await client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
    except Exception as e:
        logger.error("[answer] LLM call failed: %s", e)
        return f"[LLM Error: {e}]"
