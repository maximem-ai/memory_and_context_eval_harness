"""Plumbing for the LongMemEval-S runner.

Only benchmark-agnostic pieces live here: config loading, the LLM caller, and
retrieval rendering. Anything that encodes what the benchmark *means* (its
judge, its rubric, its aggregation) stays in benchmarks/longmemeval/.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import yaml

from benchmarks import models
from runner.types import ProviderConfig

logger = logging.getLogger("benchmarks.common")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SYNAP_BASE_URL = "https://synap-cloud-prod.maximem.ai"
DEFAULT_ANSWER_PROMPT = os.path.join("prompts", "qa_agent_subjects_used.md")

# The server applies a hard token budget to retrieved context and counts only
# the item `content` (the `memory` field here). This is the figure observed on
# the hosted service; it is used to tell a retrieval that was cut short by the
# budget from one that was limited by recall.
SERVER_TOKEN_BUDGET = 2000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_ENV_PLACEHOLDER = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def load_provider_config(path: str) -> ProviderConfig:
    """Load a Synap adapter config, expanding ${ENV_VAR} placeholders.

    SYNAP_BASE_URL falls back to the hosted service when unset.
    """
    full = path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)
    with open(full) as fh:
        raw = yaml.safe_load(fh) or {}
    extras: Dict[str, Any] = {}
    for k, v in raw.items():
        m = _ENV_PLACEHOLDER.match(v) if isinstance(v, str) else None
        if m:
            extras[k] = os.environ.get(m.group(1), "")
        else:
            extras[k] = v
    if not extras.get("base_url"):
        extras["base_url"] = DEFAULT_SYNAP_BASE_URL
    return ProviderConfig(
        api_key=extras.pop("api_key", ""),
        base_url=extras.pop("base_url", None),
        extras=extras,
    )


def missing_synap_settings(cfg: ProviderConfig) -> List[str]:
    """Names of the Synap env vars that are required but empty."""
    missing = []
    if not cfg.api_key:
        missing.append("SYNAP_API_KEY")
    if not cfg.extras.get("instance_id"):
        missing.append("SYNAP_INSTANCE_ID")
    return missing


# Fixed sampling seed for every call that supports one, so two runs over
# identical input differ as little as the provider allows. Set LME_LLM_SEED=""
# to disable it.
_raw_seed = os.environ.get("LME_LLM_SEED", "7")
LLM_SEED = int(_raw_seed) if _raw_seed.strip() else None

# Model families that reject a temperature parameter outright.
_NO_TEMPERATURE = ("o1", "o3", "o4")


class LLMUnavailable(RuntimeError):
    """The model cannot be reached or used at all (auth, unknown model).

    Stops the run: no later question can succeed either.
    """


class ContaminatedContext(RuntimeError):
    """Retrieval served this question a context we refuse to score.

    Scoped to one question, unlike LLMUnavailable: the reader and judge
    are fine, so the other questions are still measurable.
    """


class LLM:
    """Async LLM caller over LiteLLM, with usage accounting and retry.

    `spec` is "provider/model". For an OpenAI-compatible endpoint use the
    `openai` prefix and pass `api_base` (the key is taken from LLM_API_KEY,
    else OPENAI_API_KEY).
    """

    def __init__(self, spec: str, retries: int = 4,
                 api_base: Optional[str] = None):
        self.provider, self.model = models.split_model(spec)
        self.spec = spec
        self.retries = retries
        self.api_base = api_base or None
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0
        self.failures: List[Dict[str, Any]] = []
        self.resolved_models: set = set()

    @property
    def usage(self) -> Dict[str, int]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }

    async def call_with_usage(self, prompt: str, system: str = "",
                              max_tokens: int = 4096) -> Tuple[str, Dict[str, int]]:
        """Same as __call__ but returns this call's own token usage."""
        before = (self.prompt_tokens, self.completion_tokens)
        text = await self(prompt, system, max_tokens)
        return text, {"prompt_tokens": self.prompt_tokens - before[0],
                      "completion_tokens": self.completion_tokens - before[1]}

    async def __call__(self, prompt: str, system: str = "",
                       max_tokens: int = 4096) -> str:
        import litellm

        last: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                return await self._once(prompt, system, max_tokens)
            except Exception as e:
                last = e
                self.failures.append({
                    "model": self.spec, "attempt": attempt,
                    "error_type": type(e).__name__, "error": str(e)[:300],
                    "at": utc_now(),
                })
                # An auth failure or an unknown model will not fix itself;
                # fail fast and loudly.
                if isinstance(e, (litellm.AuthenticationError,
                                  litellm.NotFoundError,
                                  litellm.PermissionDeniedError)):
                    raise LLMUnavailable(f"{self.spec}: {e}") from e
                # Sustained 429 usually means the account is out of credit,
                # not that we are going too fast. Surface it.
                if (isinstance(e, litellm.RateLimitError)
                        and attempt >= self.retries - 2):
                    logger.error("%s: repeated 429, possible credit exhaustion",
                                 self.spec)
                await asyncio.sleep(2 * (2 ** attempt))
        raise LLMUnavailable(
            f"{self.spec} exhausted {self.retries} attempts: {last}")

    async def _once(self, prompt: str, system: str, max_tokens: int) -> str:
        import litellm

        # Providers differ in which sampling parameters they accept; drop the
        # unsupported ones rather than failing the call.
        litellm.drop_params = True
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}]
        kwargs: Dict[str, Any] = dict(
            model=f"{self.provider}/{self.model}", messages=msgs,
            max_tokens=max_tokens)
        if not self.model.startswith(_NO_TEMPERATURE):
            kwargs["temperature"] = 0.0
            if LLM_SEED is not None:
                kwargs["seed"] = LLM_SEED
        if self.api_base:
            kwargs["api_base"] = self.api_base
            key = os.environ.get("LLM_API_KEY")
            if key:
                kwargs["api_key"] = key
        r = await litellm.acompletion(**kwargs)
        self.calls += 1
        usage = getattr(r, "usage", None)
        if usage:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        if getattr(r, "model", None):
            self.resolved_models.add(r.model)
        return r.choices[0].message.content or ""


# ── retrieval helpers ────────────────────────────────────────────────────

_ENC = None


def content_tokens(items: List[Dict[str, Any]]) -> int:
    """Count what the server's budget counts: item `memory` text, cl100k.

    Returns -1 if tiktoken is unavailable rather than raising. This counter is
    instrumentation: it says whether the server truncated a retrieval, and it
    must never be able to fail a question.
    """
    global _ENC
    if _ENC is None:
        try:
            import tiktoken
            _ENC = tiktoken.get_encoding("cl100k_base")
        except Exception as e:  # pragma: no cover - depends on environment
            logger.warning("tiktoken unavailable (%s); token accounting disabled", e)
            return -1
    try:
        return sum(len(_ENC.encode(i.get("memory") or "")) for i in items)
    except Exception as e:
        logger.warning("token counting failed (%s); continuing", e)
        return -1


def budget_state(items: List[Dict[str, Any]], requested: int) -> Tuple[int, bool]:
    """Was this retrieval cut short by the server's token budget?

    Truncation is inferred, not reported: the SDK's ContextResponse does not
    expose `budget_exceeded`. The signal is that fewer items came back than
    were asked for AND the content is at or near the hard cap. Anything well
    under the cap was limited by recall, not by budget.
    """
    tokens = content_tokens(items)
    if tokens < 0:
        return -1, False
    truncated = len(items) < requested and tokens >= SERVER_TOKEN_BUDGET * 0.9
    return tokens, truncated


# Metadata fields the reader may be shown, and the ones it must never be.
#
# Allowed: dates that come from the conversation itself.
#   said_at           the day the user said the fact, taken from the session
#                     date sent at ingest
#   event_date        the START of the event window the fact describes
#   event_end         the end of that window, when there is one
#   event_precision   how coarse event_date is (day, month, year, ...)
#   valid_until       when the fact stops holding, if stated
#   temporal_category the extractor's label (perpetual, episode, ...)
#
# `event_date` is the start of a window, not a day: a month-precision window
# is stored as the 1st, so `2023-10-01T00:00:00` can mean "October 2023".
# That is indistinguishable without `event_precision`, which is why the two
# are allowed together and must never be separated.
#
# Never shown: extracted_at, detected_at and occurred_at. These are stamped by
# the memory system with wall-clock time at extraction or retrieval, which is
# years after the conversations. Showing one to a reader that is told the
# current date is a conversation date invites exactly the date arithmetic
# error the dataset is built to detect. Do not add a field here without
# checking its year distribution first.
METADATA_WHITELIST = ("said_at", "event_date", "event_end", "event_precision",
                      "valid_until", "temporal_category")
METADATA_BLACKLIST = ("extracted_at", "detected_at", "occurred_at")

# Whitelisted fields nested under the item's `metadata` dict, as (dict, key).
# `assertion_source` says who ASSERTED the fact ("user" or "assistant"). It is
# OFF by default and enabled by LME_RENDER_ASSERTION_SOURCE=1, because only an
# answer prompt written to use it should see it.
RENDER_ASSERTION_SOURCE_ENV = "LME_RENDER_ASSERTION_SOURCE"


def render_assertion_source_enabled() -> bool:
    return os.environ.get(RENDER_ASSERTION_SOURCE_ENV) == "1"


def _nested_whitelist() -> Tuple[Tuple[str, str], ...]:
    return (("metadata", "assertion_source"),) if render_assertion_source_enabled() else ()


# A year at or above this in rendered context means a real-world timestamp has
# leaked into the conversation. The dataset's own question dates run 2021-2024,
# so this default is correct only for the 2023 bulk of it; see
# future_years_in_metadata for why the cutoff moves with the question.
FUTURE_YEAR_CUTOFF = 2025

# How far past a conversation's own year an expiry may legitimately sit before
# we call it a leak. Two years keeps the effective cutoff at 2025 for a 2023
# conversation, while letting a 2024 conversation carry a 2025 valid_until,
# which is an ordinary expiry and not a wall-clock timestamp that escaped.
FUTURE_YEAR_GRACE = 2

_YEAR_RE = re.compile(r"\b(2\d{3})\b")


def future_years_in_metadata(items: List[Dict[str, Any]],
                             cutoff: int = FUTURE_YEAR_CUTOFF,
                             question_year: Optional[int] = None
                             ) -> List[Dict[str, Any]]:
    """Whitelisted metadata values whose year is at or above the cutoff.

    Checks the FIELD VALUES on the items, not the rendered text. Scanning the
    rendered prose instead produces false positives on user speech such as
    "2500-2800 calories/day" or a certificate that is "valid until next February".
    None of those is a leaked system timestamp, and a guard that cries wolf on
    user speech gets disabled.

    A year the MEMORY ITSELF names is also not a leak: a memory that says
    "aims to reach 50% by 2025" legitimately carries 2025 in its date fields.
    So a value is only reported when its year is NOT one the memory text
    mentions. A genuine leak, today's date stamped onto an older conversation,
    still trips it, because the memory will not be talking about this year.

    `question_year` raises the cutoff for conversations that are not from the
    dataset's main year, so an expiry a year past an early-January statement
    is not mistaken for a leak.
    """
    if question_year:
        cutoff = max(cutoff, question_year + FUTURE_YEAR_GRACE)
    out: List[Dict[str, Any]] = []
    for n, it in enumerate(items, 1):
        memory = str(it.get("memory") or "")
        claimed = {int(y) for y in _YEAR_RE.findall(memory)}
        for key in METADATA_WHITELIST:
            val = it.get(key)
            if not val:
                continue
            for y in _YEAR_RE.findall(str(val)):
                if int(y) >= cutoff and int(y) not in claimed:
                    out.append({"item": n, "field": key, "value": str(val),
                                "year": int(y),
                                "memory": memory[:80]})
    if render_subject_legend_enabled():
        # Labels are nested, so the loop above never sees them. A label's own
        # year is user speech if some memory in the context says it too.
        context_years = {int(y) for it in items
                         for y in _YEAR_RE.findall(str(it.get("memory") or ""))}
        for n, label in subject_groups(items):
            for y in _YEAR_RE.findall(label):
                if int(y) >= cutoff and int(y) not in context_years:
                    out.append({"item": n, "field": "subject_label",
                                "value": label, "year": int(y),
                                "memory": "[thing %d]" % n})
    return out


# SUBJECT GROUPS. Memories the service judges to be about the same one thing
# carry `metadata.subject_id` / `subject_label` / `subject_ordinal`, and the
# content string is prefixed `[thing N]` (N = subject_ordinal). The keys sit
# NESTED under `metadata`, so adding them to METADATA_WHITELIST renders nothing:
# it reads the top level of the item.
#
# `subject_label` is the wording of ONE member memory (a whole sentence), not
# an object name. Printed on every item it would read as an extra memory and
# invite counting mentions as occurrences, so it is rendered ONCE PER GROUP as
# a legend ahead of the memories, and only when asked for. OFF by default.
# Set LME_RENDER_SUBJECT_LABEL=1 to render the legend.
# `subject_ordinal` is per-response: never persist it or compare it across
# fetches; `subject_id` is the stable key.
RENDER_SUBJECT_LABEL_ENV = "LME_RENDER_SUBJECT_LABEL"


def render_subject_legend_enabled() -> bool:
    return os.environ.get(RENDER_SUBJECT_LABEL_ENV) == "1"


def subject_groups(items: List[Dict[str, Any]]) -> List[Tuple[int, str]]:
    """(ordinal, label) per group present in these items, ordered by ordinal.

    A group with no label, or whose ordinal is missing, is skipped; the
    marker in the content still reaches the reader either way.
    """
    seen: Dict[int, str] = {}
    for it in items:
        md = it.get("metadata") or {}
        ordinal, label = md.get("subject_ordinal"), md.get("subject_label")
        if isinstance(ordinal, int) and label and ordinal not in seen:
            seen[ordinal] = " ".join(str(label).split())
    return sorted(seen.items())


def render_subject_legend(items: List[Dict[str, Any]]) -> str:
    groups = subject_groups(items)
    if not groups:
        return ""
    lines = ["Same-thing groups (what each [thing N] marker below refers to; "
             "a name taken from one member, shown once per group -- not a "
             "memory and not evidence):"]
    lines += [f"  [thing {n}] {label}" for n, label in groups]
    return "\n".join(lines) + "\n\n"


def render_context(items: List[Dict[str, Any]],
                   include_chunks: bool = True,
                   include_metadata: bool = False) -> str:
    """Render retrieved memories for an answer prompt.

    `include_chunks` controls `source_evidence`: the raw conversation turns
    the service keeps alongside each extracted fact. When included it is
    emitted in full and never truncated, because a superseded value is often
    preserved only there: after a revision from 12% to 15%, only 15% exists as
    an extracted fact and 12% survives only inside source_evidence.

    `include_metadata` controls METADATA_WHITELIST.
    """
    nested = _nested_whitelist()
    out: List[str] = []
    for n, it in enumerate(items, 1):
        mem = (it.get("memory") or "").strip()
        parts = [f"[{n}] {mem}"]
        if include_metadata:
            for key in METADATA_WHITELIST:
                val = it.get(key)
                if val not in (None, "", []):
                    parts.append(f"    {key}: {val}")
            for outer, key in nested:
                val = (it.get(outer) or {}).get(key)
                if val not in (None, "", []):
                    parts.append(f"    {key}: {val}")
        ev = it.get("source_evidence") if include_chunks else None
        if ev:
            if isinstance(ev, (list, tuple)):
                ev_text = "\n".join(f"      - {str(e).strip()}" for e in ev)
                parts.append(f"    source_evidence:\n{ev_text}")
            else:
                parts.append(f"    source_evidence: {str(ev).strip()}")
        out.append("\n".join(parts))
    body = "\n".join(out) if out else "(no memories retrieved)"
    if render_subject_legend_enabled():
        return render_subject_legend(items) + body
    return body


CHUNK_BLOCK_OPEN = "<!-- chunks:begin -->"
CHUNK_BLOCK_ELSE = "<!-- chunks:else -->"
CHUNK_BLOCK_CLOSE = "<!-- chunks:end -->"


def apply_chunk_mode(template: str, include_chunks: bool) -> Tuple[str, int]:
    """Render an answer-prompt template for one chunk mode.

    The prompt tells the reader to use `source_evidence` (the raw conversation
    excerpts) in several places. With chunks excluded those fields are absent,
    so an unedited prompt would be telling the reader to consult something
    that is not there, and a without-chunks run would measure "no chunks AND a
    prompt asking for chunks".

    One template serves both modes so they cannot drift:

        keep only when chunks are present:
            <!-- chunks:begin -->...<!-- chunks:end -->

        different text per mode:
            <!-- chunks:begin -->with<!-- chunks:else -->without<!-- chunks:end -->

    Returns (text, blocks_applied). An unmarked template is returned unchanged
    with 0, which is what lets the caller refuse a without-chunks run rather
    than silently accept the confound.
    """
    if CHUNK_BLOCK_OPEN not in template:
        return template, 0
    out: List[str] = []
    applied = 0
    rest = template
    while CHUNK_BLOCK_OPEN in rest:
        head, _, tail = rest.partition(CHUNK_BLOCK_OPEN)
        body, _, rest = tail.partition(CHUNK_BLOCK_CLOSE)
        with_text, sep, without_text = body.partition(CHUNK_BLOCK_ELSE)
        out.append(head)
        out.append(with_text if include_chunks else (without_text if sep else ""))
        applied += 1
    out.append(rest)
    return "".join(out), applied


def count_chunk_references(template: str) -> int:
    """How many times the template names the chunk field."""
    return template.count("source_evidence")


def _resolve_prompt_path(path: Optional[str]) -> str:
    if not path or path == "default":
        path = DEFAULT_ANSWER_PROMPT
    return path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)


def load_answer_prompt(path: Optional[str] = None) -> str:
    """Read the answer prompt. None or "default" selects the shipped prompt.

    A missing path raises rather than falling back: silently scoring a
    different prompt under the requested name is worse than not running.
    """
    full = _resolve_prompt_path(path)
    if not os.path.exists(full):
        raise FileNotFoundError(f"answer prompt not found: {full}")
    with open(full) as fh:
        return fh.read()


def answer_prompt_identity(path: Optional[str] = None) -> Dict[str, Any]:
    """What prompt actually produced a run, as manifest-ready fields.

    A path alone is not enough, since the file can be edited between two runs
    that both name it. The sha256 is what makes two runs comparable, or
    provably not, after the fact.
    """
    text = load_answer_prompt(path)
    full = _resolve_prompt_path(path)
    return {"path": os.path.relpath(full, REPO_ROOT),
            "explicit": bool(path and path != "default"),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "chars": len(text)}
