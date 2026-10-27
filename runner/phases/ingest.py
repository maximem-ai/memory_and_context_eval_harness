"""
Ingest Phase — ingest benchmark sessions into a provider.

Decoupled from eval runs: ingest once per provider+benchmark pair.
Supports both blocking and async (fire-and-forget + polling) ingestion.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from runner.types import (
    IngestOptions,
    Provider,
    Benchmark,
)
from runner.ingest_checkpoint import GlobalIngestCheckpointManager, GlobalIngestCheckpoint

logger = logging.getLogger(__name__)

RATE_LIMIT_MS = 1000
QUEUE_DELAY_MS = 200


# ── Ingest ────────────────────────────────────────────────────────


async def run_ingest(
    provider: Provider,
    benchmark: Benchmark,
    provider_name: str,
    benchmark_name: str,
    checkpoint: GlobalIngestCheckpoint,
    manager: GlobalIngestCheckpointManager,
    container_tag_prefix: Optional[str] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    max_groups: Optional[int] = None,
) -> Dict[str, str]:
    """Ingest sessions into one container per question.

    Each question gets its own container_tag with only its relevant
    haystack sessions. Returns mapping of question_id -> container_tag.
    """
    questions = benchmark.get_questions()
    container_tags: Dict[str, str] = {}
    total = len(questions)

    logger.info(
        "[%s] Starting ingest for %s (%d questions)",
        provider_name, benchmark_name, total,
    )

    base_tag = container_tag_prefix or f"{benchmark_name}-{provider_name}"

    # Group questions by haystack: benchmarks where multiple questions share
    # the same conversations (Locomo, DMR) ingest once per record; benchmarks
    # with independent haystacks (LongMemEval) keep a container per question.
    groups: Dict[str, List[str]] = {}
    for q in questions:
        gid = benchmark.get_question_group_id(q.question_id)
        groups.setdefault(gid, []).append(q.question_id)

    group_ids = list(groups.keys())
    if max_groups and max_groups > 0:
        group_ids = group_ids[:max_groups]
        logger.info("[%s] Limiting ingest to first %d groups", provider_name, max_groups)
    total_groups = len(group_ids)

    for gi, group_id in enumerate(group_ids):
        container_tag = f"{base_tag}_{group_id}"
        # State is namespaced per-group so dedup is correct even if multiple
        # groups share session_ids (they shouldn't, but be defensive).
        state_key = f"{provider_name}:{base_tag}:{group_id}"

        # All questions in the group share the same haystack
        first_qid = groups[group_id][0]
        sessions = benchmark.get_haystack_sessions(first_qid)

        # Mirror the container_tag for every question in the group (eval uses
        # this map to set per-question container_tag in QuestionCheckpoint).
        for qid in groups[group_id]:
            container_tags[qid] = container_tag

        if not sessions:
            for qid in groups[group_id]:
                manager.mark_question_covered(checkpoint, state_key, qid)
            continue

        all_ingested = all(
            manager.is_session_ingested(checkpoint, state_key, s.session_id, len(s.messages))
            for s in sessions
        )
        if all_ingested:
            for qid in groups[group_id]:
                manager.mark_question_covered(checkpoint, state_key, qid)
            continue

        options = IngestOptions(container_tag=container_tag)
        t0 = time.monotonic()

        try:
            result = await provider.ingest(sessions, options)
            await provider.await_indexing(result, container_tag)

            for session in sessions:
                turn_ids = manager.generate_turn_ids(session.session_id, len(session.messages))
                manager.mark_turns_completed(checkpoint, state_key, turn_ids)

            for qid in groups[group_id]:
                manager.mark_question_covered(checkpoint, state_key, qid)
        except Exception as e:
            logger.error(
                "[%s] Ingest failed for group %s: %s",
                provider_name, group_id, e,
            )

        duration_ms = round((time.monotonic() - t0) * 1000)

        if on_progress:
            on_progress({
                "type": "ingest_group_complete",
                "group_id": group_id,
                "container_tag": container_tag,
                "sessions_ingested": len(sessions),
                "questions_in_group": len(groups[group_id]),
                "completed": gi + 1,
                "total": total_groups,
                "duration_ms": duration_ms,
            })

        if (gi + 1) % 5 == 0 or gi == total_groups - 1:
            await manager.save(checkpoint)

        if RATE_LIMIT_MS > 0 and gi < total_groups - 1:
            await asyncio.sleep(RATE_LIMIT_MS / 1000.0)

    await manager.save(checkpoint)
    logger.info(
        "[%s] Ingest complete: %d questions across %d groups, %d containers",
        provider_name, total, total_groups, len(set(container_tags.values())),
    )
