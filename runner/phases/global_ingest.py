"""
Global Ingest Phase — ingest benchmark sessions into a provider.

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
    UnifiedSession,
    SessionIngestionRecord,
)
from runner.ingest_checkpoint import GlobalIngestCheckpointManager, GlobalIngestCheckpoint

logger = logging.getLogger(__name__)

RATE_LIMIT_MS = 1000
QUEUE_DELAY_MS = 200


async def run_global_ingest(
    provider: Provider,
    benchmark: Benchmark,
    container_tag: str,
    checkpoint: GlobalIngestCheckpoint,
    manager: GlobalIngestCheckpointManager,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Ingest all benchmark sessions into a provider.

    Collects all unique sessions across questions, skips already-ingested
    ones, and routes to async or blocking path based on provider capabilities.
    """
    questions = benchmark.get_questions()
    manager.set_container_tag(checkpoint, provider.name, container_tag)

    # Collect unique sessions to ingest
    sessions_to_ingest: Dict[str, UnifiedSession] = {}
    session_question_map: Dict[str, List[str]] = {}  # session_id -> [question_ids]

    for q in questions:
        haystack = benchmark.get_haystack_sessions(q.question_id)
        for session in haystack:
            if session.session_id not in sessions_to_ingest:
                sessions_to_ingest[session.session_id] = session
                session_question_map[session.session_id] = []
            session_question_map[session.session_id].append(q.question_id)

    # Filter out already-ingested sessions
    pending_sessions: List[UnifiedSession] = []
    for sid, session in sessions_to_ingest.items():
        if not manager.is_session_ingested(checkpoint, provider.name, sid, len(session.messages)):
            pending_sessions.append(session)

    if not pending_sessions:
        logger.info("[%s] All sessions already ingested for %s", provider.name, benchmark.name)
        # Mark all questions as covered
        for q in questions:
            manager.mark_question_covered(checkpoint, provider.name, q.question_id)
        await manager.save(checkpoint)
        return

    logger.info(
        "[%s] Ingesting %d sessions (%d already done) for %s",
        provider.name, len(pending_sessions),
        len(sessions_to_ingest) - len(pending_sessions),
        benchmark.name,
    )

    # Check if provider overrides async ingestion (base class raises NotImplementedError)
    from runner.types import Provider as _BaseProvider
    has_async = (
        type(provider).ingest_fire_and_forget is not _BaseProvider.ingest_fire_and_forget
        and type(provider).check_ingestion_status is not _BaseProvider.check_ingestion_status
    )

    if has_async:
        await _run_async_ingest(
            provider, pending_sessions, container_tag,
            checkpoint, manager, session_question_map, on_progress,
        )
    else:
        await _run_blocking_ingest(
            provider, pending_sessions, container_tag,
            checkpoint, manager, session_question_map, on_progress,
        )

    # Mark questions covered
    for q in questions:
        all_covered = True
        for sid in q.haystack_session_ids:
            session = sessions_to_ingest.get(sid)
            if session and not manager.is_session_ingested(
                checkpoint, provider.name, sid, len(session.messages)
            ):
                all_covered = False
                break
        if all_covered:
            manager.mark_question_covered(checkpoint, provider.name, q.question_id)

    await manager.save(checkpoint)


async def _run_blocking_ingest(
    provider: Provider,
    sessions: List[UnifiedSession],
    container_tag: str,
    checkpoint: GlobalIngestCheckpoint,
    manager: GlobalIngestCheckpointManager,
    session_question_map: Dict[str, List[str]],
    on_progress: Optional[Callable] = None,
) -> None:
    """Ingest sessions in batches, then wait for indexing per batch."""
    options = IngestOptions(container_tag=container_tag)
    BATCH_SIZE = 50

    for batch_start in range(0, len(sessions), BATCH_SIZE):
        batch = sessions[batch_start:batch_start + BATCH_SIZE]
        t0 = time.monotonic()

        result = await provider.ingest(batch, options)
        await provider.await_indexing(result, container_tag)

        # Mark turns completed for all sessions in batch
        for session in batch:
            turn_ids = manager.generate_turn_ids(session.session_id, len(session.messages))
            manager.mark_turns_completed(checkpoint, provider.name, turn_ids)

        if on_progress:
            on_progress({
                "type": "batch_complete",
                "completed": min(batch_start + BATCH_SIZE, len(sessions)),
                "total": len(sessions),
                "batch_size": len(batch),
                "duration_ms": round((time.monotonic() - t0) * 1000),
            })

        await manager.save(checkpoint)

        if RATE_LIMIT_MS > 0 and batch_start + BATCH_SIZE < len(sessions):
            await asyncio.sleep(RATE_LIMIT_MS / 1000.0)


async def _run_async_ingest(
    provider: Provider,
    sessions: List[UnifiedSession],
    container_tag: str,
    checkpoint: GlobalIngestCheckpoint,
    manager: GlobalIngestCheckpointManager,
    session_question_map: Dict[str, List[str]],
    on_progress: Optional[Callable] = None,
) -> None:
    """Fire-and-forget ingestion followed by polling for completion."""
    options = IngestOptions(container_tag=container_tag)

    # Phase 1: Queue all sessions
    logger.info("[%s] Queuing %d sessions for async ingestion", provider.name, len(sessions))

    for i, session in enumerate(sessions):
        try:
            result = await provider.ingest_fire_and_forget([session], options)
            if result.session_ingestions:
                for si in result.session_ingestions:
                    manager.upsert_session_ingestion(
                        checkpoint, provider.name,
                        SessionIngestionRecord(
                            session_id=si.session_id,
                            ingestion_id=si.ingestion_id,
                            status="queued",
                            turn_count=len(session.messages),
                        ),
                    )
        except Exception as e:
            logger.error("[%s] Failed to queue session %s: %s", provider.name, session.session_id, e)
            manager.upsert_session_ingestion(
                checkpoint, provider.name,
                SessionIngestionRecord(
                    session_id=session.session_id,
                    status="failed",
                    error=str(e),
                ),
            )

        if on_progress:
            on_progress({
                "type": "session_queued",
                "session_id": session.session_id,
                "queued": i + 1,
                "total": len(sessions),
            })

        if QUEUE_DELAY_MS > 0:
            await asyncio.sleep(QUEUE_DELAY_MS / 1000.0)

    await manager.save(checkpoint)

    # Phase 2: Poll for completion
    logger.info("[%s] Polling for async ingestion completion", provider.name)
    poll_interval = 3.0
    max_interval = 30.0
    max_duration = 30 * 60  # 30 min
    start_time = time.monotonic()
    poll_count = 0

    while True:
        in_flight = manager.get_in_flight_sessions(checkpoint, provider.name)
        if not in_flight:
            break

        elapsed = time.monotonic() - start_time
        if elapsed > max_duration:
            logger.warning("[%s] Polling timed out after %.0fs", provider.name, elapsed)
            break

        for record in in_flight:
            if not record.ingestion_id:
                continue
            try:
                status = await provider.check_ingestion_status(record.ingestion_id)
                if status.status in ("completed", "partial_success"):
                    record.status = "completed"
                    record.memories_created = status.memories_created
                    record.completed_at = status.completed_at
                    # Mark turns completed
                    turn_ids = manager.generate_turn_ids(
                        record.session_id, record.turn_count or 0
                    )
                    manager.mark_turns_completed(checkpoint, provider.name, turn_ids)
                elif status.status == "failed":
                    record.status = "failed"
                    record.error = status.error_message
                manager.upsert_session_ingestion(checkpoint, provider.name, record)
            except Exception as e:
                logger.warning("[%s] Poll failed for %s: %s", provider.name, record.ingestion_id, e)

        poll_count += 1
        if poll_count % 5 == 0:
            await manager.save(checkpoint)
            completed = sum(
                1 for s in (manager.get_provider_state(checkpoint, provider.name).session_ingestions or [])
                if s.status == "completed"
            )
            if on_progress:
                on_progress({
                    "type": "polling_progress",
                    "completed": completed,
                    "total": len(sessions),
                    "in_flight": len(in_flight),
                })

        await asyncio.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.2, max_interval)

    await manager.save(checkpoint)


# ── Isolated Ingest ───────────────────────────────────────────────


async def run_isolated_ingest(
    provider: Provider,
    benchmark: Benchmark,
    provider_name: str,
    benchmark_name: str,
    checkpoint: GlobalIngestCheckpoint,
    manager: GlobalIngestCheckpointManager,
    container_tag_prefix: Optional[str] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, str]:
    """Ingest sessions per-question into isolated containers.

    Each question gets its own container_tag with only its relevant
    haystack sessions. Returns mapping of question_id -> container_tag.
    """
    questions = benchmark.get_questions()
    container_tags: Dict[str, str] = {}
    total = len(questions)

    logger.info(
        "[%s] Starting isolated ingest for %s (%d questions)",
        provider_name, benchmark_name, total,
    )

    base_tag = container_tag_prefix or f"{benchmark_name}-{provider_name}"

    # Use a compound key so isolated mode state is tracked separately from global
    state_key = f"{provider_name}:isolated:{base_tag}"

    for i, q in enumerate(questions):
        container_tag = f"{base_tag}_{q.question_id}"
        container_tags[q.question_id] = container_tag

        sessions = benchmark.get_haystack_sessions(q.question_id)
        if not sessions:
            manager.mark_question_covered(checkpoint, state_key, q.question_id)
            continue

        # Check if already ingested for this specific isolated container
        all_ingested = all(
            manager.is_session_ingested(checkpoint, state_key, s.session_id, len(s.messages))
            for s in sessions
        )
        if all_ingested:
            manager.mark_question_covered(checkpoint, state_key, q.question_id)
            continue

        # Ingest this question's sessions
        options = IngestOptions(container_tag=container_tag)
        t0 = time.monotonic()

        try:
            result = await provider.ingest(sessions, options)
            await provider.await_indexing(result, container_tag)

            for session in sessions:
                turn_ids = manager.generate_turn_ids(session.session_id, len(session.messages))
                manager.mark_turns_completed(checkpoint, state_key, turn_ids)

            manager.mark_question_covered(checkpoint, state_key, q.question_id)
        except Exception as e:
            logger.error(
                "[%s] Isolated ingest failed for question %s: %s",
                provider_name, q.question_id, e,
            )

        duration_ms = round((time.monotonic() - t0) * 1000)

        if on_progress:
            on_progress({
                "type": "isolated_question_complete",
                "question_id": q.question_id,
                "container_tag": container_tag,
                "sessions_ingested": len(sessions),
                "completed": i + 1,
                "total": total,
                "duration_ms": duration_ms,
            })

        if (i + 1) % 10 == 0 or i == total - 1:
            await manager.save(checkpoint)

        if RATE_LIMIT_MS > 0 and i < total - 1:
            await asyncio.sleep(RATE_LIMIT_MS / 1000.0)

    await manager.save(checkpoint)
    logger.info(
        "[%s] Isolated ingest complete: %d questions, %d containers",
        provider_name, total, len(container_tags),
    )
