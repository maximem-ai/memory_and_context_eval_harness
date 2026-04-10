"""
Global Indexing Phase — wait for all ingested data to be indexed.

Only needed for blocking ingestion paths where await_indexing
wasn't called per-session during ingestion.
"""

import logging
from typing import Any, Callable, Dict, Optional

from runner.types import IngestResult, Provider
from runner.ingest_checkpoint import GlobalIngestCheckpoint, GlobalIngestCheckpointManager

logger = logging.getLogger(__name__)


async def run_global_indexing(
    provider: Provider,
    checkpoint: GlobalIngestCheckpoint,
    manager: GlobalIngestCheckpointManager,
    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Wait for indexing to complete for all ingested sessions.

    This phase is typically a no-op since:
    - Blocking ingestion calls await_indexing per session
    - Async ingestion polls until completion during the ingest phase

    It exists as a safety net and for providers that need a global indexing step.
    """
    state = manager.get_provider_state(checkpoint, provider.name)

    # Check for any in-flight sessions that might not have completed
    in_flight = manager.get_in_flight_sessions(checkpoint, provider.name)
    if not in_flight:
        logger.info("[%s] No pending indexing — all sessions complete", provider.name)
        return

    logger.info("[%s] Waiting for %d sessions to finish indexing", provider.name, len(in_flight))

    # Build a synthetic IngestResult from in-flight sessions
    document_ids = [s.ingestion_id for s in in_flight if s.ingestion_id]
    synthetic_result = IngestResult(document_ids=document_ids)

    def _on_indexing_progress(progress):
        if on_progress:
            on_progress({
                "type": "indexing_progress",
                "completed": len(progress.completed_ids),
                "failed": len(progress.failed_ids),
                "total": progress.total,
            })

    await provider.await_indexing(synthetic_result, state.container_tag, _on_indexing_progress)
    await manager.save(checkpoint)
    logger.info("[%s] Indexing complete", provider.name)
