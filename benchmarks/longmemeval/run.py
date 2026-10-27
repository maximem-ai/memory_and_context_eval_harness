"""Run LongMemEval-S against Synap.

One Synap user per question haystack. Retrieval is accurate mode. Scoring is
LongMemEval's own judge (benchmarks/longmemeval/judge.py), one call per
question, never the in-house prompts/judge*.md, which would make the number
incomparable to anyone else's.

    python -m benchmarks.longmemeval.run --max-questions 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from benchmarks import models, runio
from benchmarks.common import (
    DEFAULT_ANSWER_PROMPT, LLM, METADATA_BLACKLIST, METADATA_WHITELIST,
    SERVER_TOKEN_BUDGET, ContaminatedContext, LLMUnavailable, apply_chunk_mode,
    answer_prompt_identity, budget_state, count_chunk_references,
    FUTURE_YEAR_GRACE, future_years_in_metadata, load_answer_prompt,
    load_provider_config, missing_synap_settings, render_assertion_source_enabled,
    render_context, render_subject_legend_enabled,
)
from benchmarks.ingest import IngestCheckpoint, UserRotation, ingest_all
from benchmarks.longmemeval import dataset as ds
from benchmarks.longmemeval import judge as lme_judge
from runner.types import SearchOptions, UnifiedMessage, UnifiedSession

logger = logging.getLogger("longmemeval")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# The SDK version the defaults were validated against.
PINNED_SDK_VERSION = "0.5.8"
# The only judge configurations that reproduce the upstream method.
UPSTREAM_JUDGES = {"openai/gpt-4o", "openai/gpt-4o-2024-08-06"}


def _read_question_ids(path: str) -> set:
    """Question ids from a JSON list, a JSON object with `rows`, or one id per
    line."""
    with open(path) as fh:
        raw = fh.read().strip()
    if raw.startswith("{") or raw.startswith("["):
        doc = json.loads(raw)
        rows = doc.get("rows", doc) if isinstance(doc, dict) else doc
        return {r["question_id"] if isinstance(r, dict) else str(r)
                for r in rows}
    return {ln.strip() for ln in raw.splitlines() if ln.strip()}


def build_sessions(q: ds.Question) -> Tuple[str, List[UnifiedSession]]:
    """One document per haystack session, dated to that session.

    document_created_at carries the session date and every turn carries its
    own timestamp. Without them a multi-day haystack collapses onto a single
    date and temporal-reasoning stops measuring memory and starts measuring
    an ingest bug.
    """
    sessions: List[UnifiedSession] = []
    for i, s in enumerate(q.sessions):
        msgs = [
            UnifiedMessage(
                role="user" if t.get("role") == "user" else "assistant",
                content=t.get("content", "") or "",
                timestamp=s.date,
            )
            for t in s.turns
        ]
        if not msgs:
            continue
        sessions.append(UnifiedSession(
            session_id=f"{q.question_id}_{s.session_id}",
            messages=msgs,
            metadata={
                "document_created_at": s.date,
                "session_index": i,
                "question_id": q.question_id,
                "session_id": s.session_id,
                # NOTE: deliberately NOT sending is_answer_session. It is
                # derived from the answer key, and the service treats
                # client-supplied metadata as extraction hints; telling the
                # memory system which session holds the answer would make
                # every number contestable.
            },
        ))
    return q.user_id, sessions


ANSWER_TEMPLATE_SUFFIX = """

## Question
{question}

## Retrieved memories
{context}

Answer the question directly and completely using only the retrieved memories.
<!-- chunks:begin -->Read every memory, including each `source_evidence` block in full — a value
that was later revised is often preserved only inside source_evidence and not
as a memory of its own. <!-- chunks:else -->Read every memory in full. <!-- chunks:end -->If the memories genuinely do not contain the answer,
say that the information is not available.

Answer:"""


SUFFIX_OPEN = "<!-- suffix:begin -->"
SUFFIX_CLOSE = "<!-- suffix:end -->"


def split_own_suffix(template: str) -> tuple:
    """Let a prompt file carry its own suffix instead of the default one.

    ANSWER_TEMPLATE_SUFFIX is appended to every prompt and is not neutral: it
    repeats instructions and ends with a bare "Answer:", and it tells the
    reader to "say that the information is not available" when the memories do
    not hold the answer. A prompt that specifies a DIFFERENT abstention
    contract would be handed both and obey neither reliably.

    A template may therefore enclose its own suffix:

        <!-- suffix:begin -->...{question}...{context}...<!-- suffix:end -->

    which replaces the default verbatim. The block must still carry the
    {question} and {context} placeholders, since nothing else renders them.
    Templates without the marker are unchanged.
    """
    if SUFFIX_OPEN not in template:
        return template, ANSWER_TEMPLATE_SUFFIX
    head, _, rest = template.partition(SUFFIX_OPEN)
    own, _, tail = rest.partition(SUFFIX_CLOSE)
    for ph in ("{question}", "{context}"):
        if ph not in own:
            raise ValueError(
                f"a prompt supplying its own suffix must include {ph} in the "
                f"{SUFFIX_OPEN}...{SUFFIX_CLOSE} block; nothing else renders it")
    return (head + tail).rstrip(), own


def build_answer_prompt(template: str, question: str, items: List[Dict[str, Any]],
                        question_date: Optional[str],
                        include_chunks: bool = True,
                        include_metadata: bool = False) -> str:
    template, raw_suffix = split_own_suffix(template)
    dateline = f"\n\n## Current date\n{question_date}" if question_date else ""
    # The suffix carries chunk guidance of its own, so it is rendered for the
    # same mode as the template.
    suffix, _ = apply_chunk_mode(raw_suffix, include_chunks)
    return template + dateline + suffix.format(
        question=question,
        context=render_context(items, include_chunks=include_chunks,
                               include_metadata=include_metadata))


def _csv(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def _sdk_version() -> str:
    try:
        from importlib.metadata import version
        return version("maximem-synap")
    except Exception:
        return "unknown"


def _pinned() -> str:
    p = os.path.join(os.path.dirname(__file__), "upstream", "PINNED_COMMIT")
    if os.path.exists(p):
        with open(p) as fh:
            for line in fh:
                if line.startswith("commit:"):
                    return line.split(":", 1)[1].strip()
    return "unknown"


async def _check_llm(llm: LLM, label: str) -> None:
    """One tiny call, so a wrong key or model id fails now and not after hours
    of ingest."""
    try:
        await llm("Reply with the single word OK.", "", 16)
    except LLMUnavailable as e:
        raise LLMUnavailable(f"{label} model check failed: {e}") from e


async def run(args) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    # Set before anything reads Question.user_id.
    try:
        prefix = ds.set_user_prefix(args.user_prefix)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2
    logger.info("scope prefix: %s", prefix)
    if prefix == ds.DEFAULT_USER_PREFIX:
        logger.warning(
            "using the default scope prefix %r. Synap caches extraction by "
            "content hash, so use a NEW --user-prefix for every fresh run.",
            prefix)

    # ---- models ----------------------------------------------------------
    for flag, spec, base in (("--answer-model", args.answer_model, args.answer_api_base),
                             ("--judge-model", args.judge_model, args.judge_api_base)):
        try:
            need = models.missing_key(spec, base)
        except ValueError as exc:
            logger.error("%s: %s", flag, exc)
            return 2
        if need:
            logger.error("%s %s needs %s to be set (see .env.example)",
                         flag, spec, need)
            return 2
    judge_is_upstream = (args.judge_model in UPSTREAM_JUDGES
                         and not args.judge_api_base)
    if not judge_is_upstream:
        logger.warning(
            "judge is %s, not the upstream openai/gpt-4o. The judge prompt is "
            "upstream's, but the score is NOT comparable to published "
            "LongMemEval numbers.", args.judge_model)

    include_chunks = args.chunks == "with"
    include_metadata = args.metadata == "on"
    try:
        tpl_raw = load_answer_prompt(args.answer_prompt)
        prompt_id = answer_prompt_identity(args.answer_prompt)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2
    _, marked = apply_chunk_mode(tpl_raw, include_chunks)
    if not include_chunks and marked == 0 and count_chunk_references(tpl_raw) > 0:
        if not args.allow_chunk_prompt_mismatch:
            logger.error(
                "--chunks without, but the answer prompt still instructs the "
                "reader to read source_evidence (%d references, 0 marked "
                "blocks). That run would measure 'no chunks AND a prompt "
                "asking for chunks'. Mark the guidance with <!-- chunks:begin "
                "-->/<!-- chunks:end -->, or pass "
                "--allow-chunk-prompt-mismatch to accept the confound.",
                count_chunk_references(tpl_raw))
            return 2
        logger.warning("running --chunks without on an unmarked prompt; the "
                       "number carries a prompt/context mismatch")
    logger.info("answer model: %s | judge model: %s | chunks: %s | metadata: %s",
                args.answer_model, args.judge_model, args.chunks, args.metadata)

    # ---- dataset ---------------------------------------------------------
    data_path = args.data if os.path.isabs(args.data) else os.path.join(
        REPO_ROOT, args.data)
    try:
        all_questions = ds.load(data_path)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2
    # Corpus integrity is checked over the whole file, before any filtering or
    # --max-questions slicing, so even a 3-question pass proves it is running
    # on the S split.
    report = ds.validate(all_questions)
    logger.info("dataset: %s", json.dumps({k: v for k, v in report.items()
                                           if k != "problems"}))
    if not report["ok"]:
        logger.error("dataset validation failed, refusing to run: %s",
                     report["problems"])
        return 2
    dataset_sha = ds.file_sha256(data_path)

    questions = (all_questions[:args.max_questions] if args.max_questions
                 else all_questions)
    try:
        questions, selection = ds.select(
            questions,
            include_types=_csv(args.question_types),
            exclude_types=_csv(args.exclude_question_types),
            exclude_abstention=args.exclude_abstention,
            include_ids=(_read_question_ids(args.question_ids)
                         if args.question_ids else None))
    except ValueError as exc:
        logger.error("%s", exc)
        return 2
    selection["max_questions"] = args.max_questions
    selection["is_partial"] = len(questions) != len(all_questions)
    if selection["is_partial"]:
        logger.info("selection: %s", json.dumps(selection))
        logger.info("PARTIAL PASS: %d of %d questions (%d answerable). "
                    "Accuracy is over this subset, not the full split.",
                    selection["selected_questions"], ds.EXPECTED_QUESTIONS,
                    selection["selected_answerable"])
    if not questions:
        logger.error("selection left 0 questions")
        return 2

    # ---- synap config ----------------------------------------------------
    try:
        cfg = load_provider_config(args.config)
    except FileNotFoundError:
        logger.error("config not found: %s", args.config)
        return 2
    missing = missing_synap_settings(cfg)
    if missing:
        logger.error("missing required settings: %s (see .env.example)",
                     ", ".join(missing))
        return 2
    # --retrieval-mode overrides the config so a fast-mode run needs no edit
    # to the config file. Both keys are set because the adapter reads
    # retrieval_mode first and falls back to mode.
    if args.retrieval_mode:
        cfg.extras["retrieval_mode"] = args.retrieval_mode
        cfg.extras["mode"] = args.retrieval_mode
    mode = cfg.extras.get("retrieval_mode") or cfg.extras.get("mode")
    # Accurate stays the only mode you get without saying so. Fast skips graph
    # traversal, so a fast run does not measure the same system and its
    # numbers are not comparable to any accurate run.
    if str(mode).lower() != "accurate":
        if args.retrieval_mode and str(mode).lower() == str(args.retrieval_mode).lower():
            logger.warning(
                "retrieval mode is %r by explicit --retrieval-mode. Fast mode "
                "skips graph traversal, so these scores are NOT comparable to "
                "accurate-mode runs; the manifest records the mode.", mode)
        else:
            logger.error("retrieval mode is %r, must be 'accurate'. Pass "
                         "--retrieval-mode fast to run it deliberately.", mode)
            return 2

    answer_llm = LLM(args.answer_model, api_base=args.answer_api_base)
    judge_llm = LLM(args.judge_model, api_base=args.judge_api_base)
    if not args.skip_model_check:
        try:
            await _check_llm(answer_llm, "answer")
            await _check_llm(judge_llm, "judge")
        except LLMUnavailable as exc:
            logger.error("%s", exc)
            return 2
        # The model check is not part of the measured run.
        for llm in (answer_llm, judge_llm):
            llm.calls = llm.prompt_tokens = llm.completion_tokens = 0
            llm.failures.clear()

    from adapters.synap_adapter import SynapProvider
    provider = SynapProvider()
    await provider.initialize(cfg)

    run_id = args.run_id or f"lme_s_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    out = runio.run_dir(os.path.join(REPO_ROOT, "results"), "longmemeval", run_id)
    logger.info("run dir: %s", out)

    # A cohort's checkpoint and user-remap state belong to its prefix, so a
    # stale rotation from another cohort can never be applied to a scope that
    # was never wedged.
    ckpt_tag = ds.checkpoint_tag(prefix, args.checkpoint_tag)
    ckpt_path = os.path.join(REPO_ROOT, "checkpoints",
                             f"longmemeval_{ckpt_tag}.json")
    rotation = UserRotation(os.path.join(
        REPO_ROOT, "checkpoints", f"lme_{ckpt_tag}_usermap.json"))

    sdk_version = _sdk_version()
    if sdk_version != PINNED_SDK_VERSION:
        logger.warning("maximem-synap %s is installed; the defaults were "
                       "validated against %s", sdk_version, PINNED_SDK_VERSION)

    redacted_cfg = {k: ("<set>" if k == "instance_id" and v else v)
                    for k, v in cfg.extras.items()}
    manifest: Dict[str, Any] = {
        "run_id": run_id,
        "benchmark": "longmemeval",
        "split": "S",
        "started_at": runio.utc_now(),
        "harness": {
            "judge_source": "vendored upstream xiaowu0162/LongMemEval",
            "judge_pinned_commit": _pinned(),
            "judge_is_question_type_specific": True,
            "judge_calls_per_question": 1,
            "abstention_selector": "'_abs' in question_id",
            "in_house_judges_used": False,
        },
        "dataset": {
            "file": os.path.relpath(data_path, REPO_ROOT),
            "sha256": dataset_sha,
            **{k: v for k, v in report.items() if k not in ("problems", "ok")},
        },
        "synap": {
            "sdk_version": sdk_version,
            "sdk_version_expected": PINNED_SDK_VERSION,
            "base_url": cfg.base_url,
            "mode": mode,
            "max_results": cfg.extras.get("retrieval_max_results"),
            "inline_turn_timestamps": cfg.extras.get("inline_turn_timestamps"),
            "grpc_listen": cfg.extras.get("enable_grpc_listen"),
            "server_token_budget_observed": SERVER_TOKEN_BUDGET,
            "question_date_sent_to_search": False,
        },
        "reader": {
            "answer_prompt": prompt_id,
            "chunks": args.chunks,
            "chunks_field": "source_evidence",
            "include_chunks": include_chunks,
            "metadata": args.metadata,
            "include_metadata": include_metadata,
            "metadata_whitelist": list(METADATA_WHITELIST) if include_metadata else [],
            "metadata_excluded": list(METADATA_BLACKLIST),
            "render_subject_label": render_subject_legend_enabled(),
            "render_assertion_source": render_assertion_source_enabled(),
        },
        "models": {
            "answer_model": args.answer_model,
            "judge_model": args.judge_model,
            "custom_api_base": {"answer": bool(args.answer_api_base),
                                "judge": bool(args.judge_api_base)},
            "judge_matches_upstream": judge_is_upstream,
            "llm_calls_per_question": 2,
        },
        "concurrency": {"qa": args.concurrency, "search": args.search_concurrency,
                        "ingest_users": args.ingest_concurrency,
                        "ingest_batch": args.batch_size},
        "provider_config": redacted_cfg,
        "user_remap": rotation.map,
        "isolation": {"scheme": "one Synap user per question haystack",
                      "unique_user_ids": report.get("unique_user_ids"),
                      "user_prefix": prefix,
                      "checkpoint_tag": ckpt_tag},
        # A filtered pass scores a different denominator. Recorded here so no
        # later reader can mistake it for a full-split number.
        "selection": selection,
    }
    runio.write_manifest(out, manifest)

    # ---- ingest ----------------------------------------------------------
    if not args.skip_ingest:
        cp = IngestCheckpoint(
            ckpt_path,
            # Over the WHOLE split, never the selection: a partial pass and a
            # later full pass are the same cohort and must share one
            # checkpoint identity, or the second is refused as a mismatch.
            fingerprint=ds.fingerprint(all_questions),
            benchmark="longmemeval", run_id=run_id)
        t0 = time.monotonic()
        doc_dir = os.path.join(out, "ingested") if args.dump_ingested else None
        reports = await ingest_all(provider, questions, build_sessions, cp,
                                   concurrency=args.ingest_concurrency,
                                   batch_size=args.batch_size, rotation=rotation,
                                   doc_dump_dir=doc_dir)
        if doc_dir:
            manifest["ingested_documents_dir"] = os.path.relpath(doc_dir, REPO_ROOT)
        manifest["ingest"] = {
            "duration_s": round(time.monotonic() - t0, 1),
            "users": len(reports),
            "resumed_users": sum(1 for r in reports if r.get("resumed")),
            "submitted": sum(r.get("submitted", 0) for r in reports),
            "failed": sum(r.get("failed", 0) for r in reports),
            "vanished": sum((r.get("indexing") or {}).get("vanished", 0) for r in reports),
            "timed_out": sum((r.get("indexing") or {}).get("timed_out", 0) for r in reports),
            "transient_errors": len(provider.transient_errors),
            "zero_extraction_users": sum(1 for r in reports if r.get("zero_extraction")),
            "unverified": [r["user_id"] for r in reports
                           if r.get("verification")
                           and not r["verification"]["verified"]],
        }
        manifest["user_remap"] = dict(rotation.map)
        runio.write_manifest(out, manifest)
        logger.info("ingest done: %s", json.dumps(manifest["ingest"]))
        if args.ingest_only:
            manifest["finished_at"] = runio.utc_now()
            manifest["status"] = "ingest_only"
            runio.write_manifest(out, manifest)
            return 0

    # ---- cohort preflight ------------------------------------------------
    # The failure this exists to stop: ingesting under one prefix and
    # evaluating under another. Retrieval against a scope that was never
    # written does not error. It returns nothing, on every question, and the
    # run completes with a near-zero score that looks like a memory failure
    # rather than a flag we forgot to pass.
    if not args.allow_uningested_cohort:
        ro = IngestCheckpoint(ckpt_path)
        not_ingested = [q.question_id for q in questions
                        if not ro.done_sessions(rotation.effective(q.user_id))]
        if not_ingested:
            logger.error(
                "cohort preflight failed: %d of %d selected scopes have no "
                "ingest evidence under prefix %r (checkpoint %s). First few: "
                "%s. Either this prefix was never ingested, or the ingest ran "
                "under a different --user-prefix / --checkpoint-tag. Pass "
                "--allow-uningested-cohort to override.",
                len(not_ingested), len(questions), prefix, ckpt_tag,
                ", ".join(not_ingested[:5]))
            manifest["preflight"] = {"ok": False,
                                     "missing_scopes": len(not_ingested),
                                     "examples": not_ingested[:20]}
            manifest["status"] = "preflight_failed"
            runio.write_manifest(out, manifest)
            return 2
        manifest["preflight"] = {"ok": True,
                                 "scopes_with_ingest_evidence": len(questions)}

    # ---- QA --------------------------------------------------------------
    template, chunk_blocks = apply_chunk_mode(tpl_raw, include_chunks)
    logger.info("answer prompt: %d chunk block(s) applied for chunks=%s, "
                "%d source_evidence reference(s) remain",
                chunk_blocks, args.chunks, count_chunk_references(template))
    manifest["reader"]["prompt_chunk_blocks_applied"] = chunk_blocks
    manifest["reader"]["prompt_chunk_refs_remaining"] = count_chunk_references(template)
    runio.write_manifest(out, manifest)

    writer = runio.QuestionWriter(out)
    # Only questions that finished cleanly are skipped on resume. A question
    # that errored is tried again, and its newer row replaces the older one.
    done = {r["question_id"] for r in runio.read_rows(out)
            if lme_judge.is_scored(r)}
    if done:
        logger.info("resuming: %d questions already scored", len(done))
    sem = asyncio.Semaphore(args.concurrency)
    # Retrieval gets its own, tighter budget. The three stages of a question
    # are serial, so one overall limit would throttle the LLM calls just to
    # protect the vector store. This bounds only the Synap fetches.
    search_sem = asyncio.Semaphore(args.search_concurrency)
    halted = asyncio.Event()

    async def one(q: ds.Question) -> None:
        if q.question_id in done or halted.is_set():
            return
        async with sem:
            if halted.is_set():
                return
            row = runio.QuestionRow(
                question_id=q.question_id, user_id=rotation.effective(q.user_id),
                question_type=q.question_type, question=q.question,
                gold_answer=q.answer, abstention=q.is_abstention,
                budget_limit_tokens=SERVER_TOKEN_BUDGET,
            )
            try:
                t0 = time.monotonic()
                # The question's date is NOT sent to retrieval: neither as a
                # request field nor inside the query text.
                async with search_sem:
                    items = await provider.search(
                        q.question,
                        SearchOptions(container_tag=rotation.effective(q.user_id),
                                      limit=args.max_results,
                                      threshold=None))
                fm = getattr(provider, "last_fetch_meta", None) or {}
                row.retrieval_source = fm.get("source")
                row.retrieval_correlation_id = fm.get("correlation_id")
                row.retrieval_attempts = fm.get("attempts", 1)
                row.retrieval_health = fm.get("retrieval_health")
                row.search_ms = round((time.monotonic() - t0) * 1000, 1)
                row.retrieved_context = items
                row.retrieved_count = len(items)
                row.retrieval_empty = not items
                row.retrieved_content_tokens, row.budget_truncated = budget_state(
                    items, args.max_results)

                if include_metadata:
                    # The cutoff moves with the conversation: see
                    # future_years_in_metadata.
                    qyear = None
                    try:
                        qyear = int(str(q.question_date)[:4])
                    except (TypeError, ValueError):
                        pass
                    leaked = future_years_in_metadata(items, question_year=qyear)
                    if leaked and not args.allow_future_dates:
                        logger.error(
                            "%s: whitelisted metadata carries a year past "
                            "the %d-year grace on this conversation's own year, "
                            "which means a real-world timestamp has leaked: "
                            "%s. Refusing to score a contaminated context; "
                            "pass --allow-future-dates to override.",
                            q.question_id, FUTURE_YEAR_GRACE,
                            json.dumps(leaked[:3]))
                        row.future_dated_metadata = leaked[:10]
                        # Scoped to this question, not the run: leaving the
                        # row unscored satisfies the guard, while halting
                        # would throw away every clean question too.
                        raise ContaminatedContext(
                            f"future-dated metadata on {q.question_id}")
                row.answer_prompt = build_answer_prompt(
                    template, q.question, items, q.question_date,
                    include_chunks=include_chunks,
                    include_metadata=include_metadata)
                row.ingested_session_ids = [s.session_id for s in q.sessions]
                if args.dump_ingested:
                    row.ingested_doc_ref = os.path.join(
                        "ingested", f"{rotation.effective(q.user_id)}.json")

                t1 = time.monotonic()
                row.generated_answer, au = await answer_llm.call_with_usage(
                    row.answer_prompt)
                row.answer_ms = round((time.monotonic() - t1) * 1000, 1)
                row.answer_prompt_tokens = au["prompt_tokens"]
                row.answer_completion_tokens = au["completion_tokens"]
                if not row.generated_answer.strip():
                    # No answer is a failure of the reader call, not a memory
                    # miss, so it must not be judged "wrong".
                    raise ValueError("answer model returned an empty reply")

                # One judge call, upstream's prompt, handed the full answer.
                t2 = time.monotonic()
                before = (judge_llm.prompt_tokens, judge_llm.completion_tokens)
                v = await lme_judge.score_async(
                    q.question_id, q.question_type, q.question, q.answer,
                    row.generated_answer, judge_llm)
                row.judge_ms = round((time.monotonic() - t2) * 1000, 1)
                row.judge_prompt = v.prompt
                row.judge_raw = v.raw
                row.judge_prompt_tokens = judge_llm.prompt_tokens - before[0]
                row.judge_completion_tokens = judge_llm.completion_tokens - before[1]
            except ContaminatedContext as e:
                row.error = f"contaminated context: {e}"
                logger.error("not scoring %s: %s", q.question_id, e)
            except LLMUnavailable as e:
                row.error = f"LLM unavailable: {e}"
                logger.error("halting: %s", e)
                halted.set()
            except Exception as e:
                row.error = f"{type(e).__name__}: {str(e)[:300]}"
                logger.warning("question %s failed: %s", q.question_id, row.error)
            writer.write(row)

    await asyncio.gather(*(one(q) for q in questions))
    writer.close()

    wanted = {q.question_id for q in questions}
    rows = [r for r in runio.read_rows(out) if r["question_id"] in wanted]
    agg = lme_judge.aggregate(rows)
    clean = [r for r in rows if not r.get("error")]
    n_scored = sum(1 for r in rows if lme_judge.is_scored(r))
    summary = {
        "run_id": run_id, "benchmark": "longmemeval", "split": "S",
        # Headline is the answerable set only. The abstention questions are
        # run and scored, reported under "abstention", and excluded here.
        "accuracy": agg["accuracy"], "n": agg["n"], "correct": agg["correct"],
        "accuracy_scope": agg["scope"],
        "all_questions": agg["all_questions"],
        "scored": n_scored,
        "attempted": len(rows), "expected": len(questions),
        # Rows with an error are never counted as scored or as wrong.
        "errors": sum(1 for r in rows if r.get("error")),
        "not_attempted": len(questions) - len(rows),
        "abstention": agg["abstention"], "answerable": agg["answerable"],
        "retrieval": runio.retrieval_summary(clean),
        "timing": runio.timing_summary(clean),
        "llm_usage": {"answer": answer_llm.usage, "judge": judge_llm.usage},
        "llm_resolved_models": {"answer": sorted(answer_llm.resolved_models),
                                "judge": sorted(judge_llm.resolved_models)},
        "synap_transient_errors": len(provider.transient_errors),
        "halted_early": halted.is_set(),
    }
    complete = (not halted.is_set() and n_scored == len(questions))
    runio.write_json(out, "by_category.json", agg["by_question_type"])
    runio.write_json(out, "summary.json", summary)
    manifest["finished_at"] = runio.utc_now()
    manifest["status"] = ("halted" if halted.is_set()
                          else "complete" if complete else "incomplete")
    manifest["models"]["resolved"] = summary["llm_resolved_models"]
    manifest["synap_transient_error_log"] = provider.transient_errors[:200]
    runio.write_manifest(out, manifest)
    _write_report(out, manifest, summary, agg)
    logger.info("LongMemEval-S accuracy %.4f over %d answerable questions "
                "(abstention %s of %d, excluded from the headline); "
                "%d row(s) with errors excluded; status %s",
                agg["accuracy"], agg["n"],
                agg["abstention"]["correct"], agg["abstention"]["n"],
                summary["errors"], manifest["status"])
    return 0 if complete else 1


def _pct(x: Optional[float]) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def _write_report(out, manifest, summary, agg) -> None:
    L = []
    L.append("# LongMemEval-S — Synap\n")
    L.append(f"Run `{manifest['run_id']}` · {manifest['started_at']} → "
             f"{manifest.get('finished_at', '')} · status `{manifest.get('status')}`\n")
    L.append(f"**Accuracy: {_pct(summary['accuracy'])}** "
             f"({summary['correct']}/{summary['n']} answerable questions)\n")
    aq = summary["all_questions"]
    L.append("Abstention questions are excluded from the headline: they test "
             "whether the model correctly refuses, are graded against a "
             "different prompt, and measure something the rest of the "
             "benchmark does not. They are still run and scored, and are "
             f"reported below. Including them the figure is "
             f"{_pct(aq['accuracy'])} ({aq['correct']}/{aq['n']}).\n")
    if summary["errors"] or summary["not_attempted"] or summary["scored"] != summary["expected"]:
        L.append(f"> **Incomplete:** {summary['scored']} of {summary['expected']} "
                 f"questions scored. {summary['errors']} row(s) ended in an error and "
                 f"{summary['not_attempted']} were not attempted. Neither kind is "
                 f"counted in any accuracy figure; resume the run to retry them.\n")
    sel = manifest["selection"]
    if sel.get("is_partial"):
        L.append(f"> Partial pass: {sel['selected_questions']} of "
                 f"{ds.EXPECTED_QUESTIONS} questions. Not a full-split number.\n")
    L.append("\n## By question type — answerable only\n")
    L.append("| type | n | correct | accuracy |")
    L.append("|---|---:|---:|---:|")
    for t, b in agg["by_question_type"].items():
        L.append(f"| {t} | {b['n']} | {b['correct']} | {_pct(b['accuracy'])} |")
    L.append("\n## Abstention vs answerable\n")
    L.append("| slice | n | correct | accuracy |")
    L.append("|---|---:|---:|---:|")
    for k in ("abstention", "answerable"):
        b = summary[k]
        L.append(f"| {k} | {b['n']} | {b['correct']} | {_pct(b['accuracy'])} |")
    r = summary["retrieval"]
    L.append("\n## Retrieval\n")
    L.append(f"- empty retrievals: {r['empty_retrievals']} ({r['empty_pct']}%)")
    L.append(f"- mean items/question: {r['mean_items']}")
    L.append(f"- memory-field tokens mean {r['memory_field_tokens']['mean']}, "
             f"peak {r['memory_field_tokens']['peak']} of {SERVER_TOKEN_BUDGET} "
             f"(the server's budget applies to this field only)")
    L.append(f"- answer prompt tokens mean {r['answer_prompt_tokens']['mean']}, "
             f"peak {r['answer_prompt_tokens']['peak']} "
             f"(what the reader actually sees, source_evidence included)")
    L.append(f"- budget-truncated questions: {r['budget_truncated']}")
    L.append(f"- Synap transient errors retried: {summary['synap_transient_errors']}")
    L.append("\n## Scoring\n")
    m = manifest["models"]
    L.append(f"- judge: `{m['judge_model']}`, one call per question, LongMemEval's "
             f"own question-type-specific prompts, vendored at "
             f"`{manifest['harness']['judge_pinned_commit'][:12]}`")
    if not m["judge_matches_upstream"]:
        L.append("- **the judge is not the upstream openai/gpt-4o, so this score "
                 "is not comparable to published LongMemEval numbers**")
    L.append("- the in-house `prompts/judge*.md` were NOT used")
    L.append(f"- answer model: `{m['answer_model']}`")
    L.append(f"- retrieval mode: `{manifest['synap']['mode']}`, "
             f"max_results {manifest['synap']['max_results']}")
    L.append(f"- dataset: `{manifest['dataset']['file']}` "
             f"(sha256 `{manifest['dataset']['sha256'][:12]}`)")
    with open(os.path.join(out, "REPORT.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(REPO_ROOT, ".env"))
    except ImportError:  # pragma: no cover
        pass

    p = argparse.ArgumentParser(
        description="Run LongMemEval-S against Synap",
        epilog="Use a NEW --user-prefix for every fresh run: Synap caches "
               "extraction by content hash.")
    p.add_argument("--config", default="configs/synap_longmemeval_s.yaml")
    p.add_argument("--data", default=os.path.relpath(ds.DEFAULT_DATA, REPO_ROOT),
                   help="LongMemEval S json (default: %(default)s). The run "
                        "refuses any file that is not the S split.")
    p.add_argument("--answer-prompt", default="default",
                   help=f"'default' selects {DEFAULT_ANSWER_PROMPT}; any other "
                        "value is a path to a prompt file. Recorded in the "
                        "manifest with its sha256.")
    p.add_argument("--retrieval-mode", choices=("accurate", "fast"), default=None,
                   help="override the config's retrieval mode. Accurate is the "
                        "default; fast skips graph traversal and its scores "
                        "are not comparable to accurate-mode runs")
    p.add_argument("--answer-model", default=models.DEFAULT_ANSWER_MODEL,
                   help="provider/model (default: %(default)s)")
    p.add_argument("--judge-model", default=models.DEFAULT_JUDGE_MODEL,
                   help="provider/model (default: %(default)s). Any other judge "
                        "makes the score non-comparable to upstream.")
    p.add_argument("--answer-api-base", default=None,
                   help="base URL of an OpenAI-compatible endpoint for the "
                        "answer model (use the openai/ prefix; key from "
                        "LLM_API_KEY)")
    p.add_argument("--judge-api-base", default=None,
                   help="base URL of an OpenAI-compatible endpoint for the judge")
    p.add_argument("--skip-model-check", action="store_true",
                   help="skip the one-token call that checks each model id and "
                        "key before ingest starts")
    p.add_argument("--chunks", default="with", choices=models.CHUNK_MODES,
                   help="'with' includes source_evidence (the raw conversation "
                        "turns) in the answer prompt; 'without' gives the reader "
                        "only the extracted memories.")
    p.add_argument("--allow-chunk-prompt-mismatch", action="store_true",
                   help="run --chunks without even though the prompt still "
                        "references source_evidence (accepts the confound)")
    p.add_argument("--metadata", default="on", choices=("off", "on"),
                   help="'on' shows the reader said_at, event_date, event_end, "
                        "event_precision, valid_until and temporal_category")
    p.add_argument("--allow-future-dates", action="store_true",
                   help="score even if whitelisted metadata carries a year "
                        "far past the conversation's own (a leaked real-world "
                        "timestamp)")
    p.add_argument("--max-results", type=int, default=50)
    p.add_argument("--batch-size", type=int, default=5,
                   help="sessions per batch_create call")
    p.add_argument("--concurrency", type=int, default=5,
                   help="questions answered and judged at once")
    p.add_argument("--search-concurrency", type=int, default=3,
                   help="cap on simultaneous Synap retrievals, separate from "
                        "--concurrency")
    p.add_argument("--ingest-concurrency", type=int, default=12,
                   help="Synap users ingested at once")
    p.add_argument("--max-questions", type=int, default=None,
                   help="run only the first N questions (the whole file is "
                        "still validated)")
    p.add_argument("--run-id", default=None,
                   help="reuse a run id to resume that run's results")
    p.add_argument("--checkpoint-tag", default="full")
    p.add_argument("--user-prefix", default=ds.DEFAULT_USER_PREFIX,
                   help="Synap scope prefix, [a-z0-9_]+. Use a NEW one for "
                        "every fresh run. MUST match between the ingest and "
                        "the eval of one run: retrieval fetches with this "
                        "prefix too, and a mismatch returns nothing on every "
                        "question instead of failing.")
    p.add_argument("--question-types", default=None,
                   help="comma-separated question types to include "
                        "(default: all)")
    p.add_argument("--exclude-question-types", default=None,
                   help="comma-separated question types to skip")
    p.add_argument("--exclude-abstention", action="store_true",
                   help="drop the 30 unanswerable questions. They are not a "
                        "question_type (they span four types and are marked "
                        "only by the '_abs' id suffix), so "
                        "--exclude-question-types cannot reach them.")
    p.add_argument("--question-ids", default=None,
                   help="Path to a JSON or newline list of question ids; the "
                        "whole run is restricted to them. An unknown id is an "
                        "error, not an empty result.")
    p.add_argument("--allow-uningested-cohort", action="store_true",
                   help="skip the preflight that refuses to evaluate scopes "
                        "with no ingest evidence under this prefix")
    p.add_argument("--dump-ingested", action="store_true",
                   help="write the exact documents sent to Synap under the "
                        "run directory (large on the S split)")
    p.add_argument("--skip-ingest", action="store_true")
    p.add_argument("--ingest-only", action="store_true")
    args = p.parse_args()
    try:
        return asyncio.run(run(args))
    except LLMUnavailable as exc:
        logging.getLogger("longmemeval").error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
