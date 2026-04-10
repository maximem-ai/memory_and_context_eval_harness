"""
Answer Phase — generate hypotheses for each question using an LLM.

Reads search results from disk, builds a prompt with context, and calls
the configured answering model.
"""

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

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

DEFAULT_SYSTEM_PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "prompts", "system_prompt.md",
)

DEFAULT_ANSWER_TEMPLATE = """Question: {question}
Question Date: {question_date}

Retrieved Context (raw JSON from memory provider):
{context}

Answer:"""


def _load_default_system_prompt() -> str:
    """Load prompts/system_prompt.md as the default system prompt."""
    try:
        with open(DEFAULT_SYSTEM_PROMPT_PATH, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.warning("Default system prompt not found at %s", DEFAULT_SYSTEM_PROMPT_PATH)
        return "You are a helpful assistant with access to conversation memory."


async def run_answer_phase(
    benchmark: Benchmark,
    checkpoint: RunCheckpoint,
    checkpoint_mgr: CheckpointManager,
    answering_model: str,
    system_prompt: str = "",
    provider: Optional[Provider] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Run the answer phase for all questions with completed search."""
    # Load default system prompt from prompts/system_prompt.md if none provided
    if not system_prompt:
        system_prompt = _load_default_system_prompt()

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

    async def answer_one(question_id: str, index: int) -> Dict[str, Any]:
        qcp = checkpoint.questions[question_id]
        t0 = time.monotonic()

        checkpoint_mgr.update_answer_phase(
            checkpoint, question_id, status="in_progress",
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        try:
            # Load search results from disk
            search_data = checkpoint_mgr.load_search_results(checkpoint.run_id, question_id)
            results = search_data.get("results", []) if search_data else []

            # Build prompt
            prompt = _build_answer_prompt(
                qcp.question,
                results,
                qcp.question_date,
                provider,
                system_prompt,
            )

            # Call LLM
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

    await execute_concurrent(pending, concurrency, "answer", answer_one)


def _build_answer_prompt(
    question: str,
    search_results: list,
    question_date: Optional[str],
    provider: Optional[Provider],
    system_prompt: str,
) -> str:
    """Build the answer prompt, using provider custom prompt if available."""
    # Check for provider custom prompt
    if provider and provider.prompts and provider.prompts.answer_prompt:
        ap = provider.prompts.answer_prompt
        if callable(ap):
            return ap(question, search_results, question_date)
        # String template
        context_str = build_context_string(search_results)
        return ap.replace("{question}", question).replace(
            "{context}", context_str
        ).replace("{question_date}", question_date or "N/A")

    # Default prompt (system prompt is sent separately as system message)
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
