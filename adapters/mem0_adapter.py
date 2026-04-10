"""
Mem0 Provider — wraps the mem0ai SDK (hosted platform).

mem0 performs its own fact extraction on add() and returns extracted
memories on search(). Implements the Provider interface with
container_tag mapped to mem0's user_id for data isolation.
"""

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from adapters.base_adapter import (
    ConcurrencyConfig,
    IngestOptions,
    IngestResult,
    IngestionStatus,
    Provider,
    ProviderConfig,
    SearchOptions,
    SessionIngestion,
    UnifiedSession,
)

logger = logging.getLogger(__name__)


class Mem0Provider(Provider):
    """Provider for the mem0 hosted memory platform."""

    name = "mem0"
    concurrency = ConcurrencyConfig(default=50)

    def __init__(self):
        self.client = None
        self.top_k = 10

    async def initialize(self, config: ProviderConfig) -> None:
        try:
            from mem0 import MemoryClient
        except ImportError:
            raise ImportError(
                "mem0ai package is required. Install with: pip install mem0ai"
            )

        api_key = config.api_key or os.environ.get("MEM0_API_KEY", "")
        if not api_key:
            raise ValueError("MEM0_API_KEY is required (config or env var)")

        init_kwargs: Dict[str, Any] = {"api_key": api_key}
        org_id = config.extras.get("org_id") or os.environ.get("MEM0_ORG_ID")
        project_id = config.extras.get("project_id") or os.environ.get("MEM0_PROJECT_ID")
        if org_id:
            init_kwargs["org_id"] = org_id
        if project_id:
            init_kwargs["project_id"] = project_id

        self.client = MemoryClient(**init_kwargs)
        self.top_k = config.extras.get("top_k", 10)
        logger.info("Mem0 provider initialised (hosted platform)")

    async def ingest(
        self, sessions: List[UnifiedSession], options: IngestOptions
    ) -> IngestResult:
        """Ingest sessions into mem0. Each session's messages are sent as a batch."""
        document_ids: List[str] = []
        session_ingestions: List[SessionIngestion] = []

        for session in sessions:
            messages = [
                {"role": msg.role, "content": msg.content}
                for msg in session.messages
            ]
            meta = dict(session.metadata or {})
            meta["session_id"] = session.session_id

            try:
                result = self.client.add(
                    messages=messages,
                    user_id=options.container_tag,
                    metadata=meta,
                )
                event_id = None
                if isinstance(result, dict):
                    event_id = result.get("event_id")
                    results_list = result.get("results", [])
                    for r in results_list:
                        doc_id = r.get("id", str(uuid.uuid4()))
                        document_ids.append(doc_id)
                elif isinstance(result, list):
                    for r in result:
                        doc_id = r.get("id", str(uuid.uuid4()))
                        document_ids.append(doc_id)

                session_ingestions.append(
                    SessionIngestion(
                        session_id=session.session_id,
                        ingestion_id=event_id,
                        status="completed",
                        turn_count=len(session.messages),
                        memories_created=len(document_ids),
                    )
                )
            except Exception as e:
                logger.error("Mem0 ingest failed for session %s: %s", session.session_id, e)
                session_ingestions.append(
                    SessionIngestion(
                        session_id=session.session_id,
                        status="failed",
                        error=str(e),
                    )
                )

        return IngestResult(
            document_ids=document_ids,
            session_ingestions=session_ingestions,
        )

    # Mem0's client.add() is synchronous — memories are created immediately.
    # No need for fire-and-forget or polling. The blocking ingest() path
    # handles everything and marks sessions as completed right away.

    async def await_indexing(
        self, result: IngestResult, container_tag: str, on_progress=None
    ) -> None:
        """Mem0 creates memories synchronously — nothing to wait for."""
        pass

    async def search(self, query: str, options: SearchOptions) -> List[Any]:
        """Search extracted memories for a container (user)."""
        limit = options.limit or self.top_k
        try:
            results = self.client.search(
                query=query,
                user_id=options.container_tag,
                filters={"user_id": options.container_tag},
                top_k=limit,
                output_format="v1.1",
            )
            items = results if isinstance(results, list) else results.get("results", [])
            return items
        except Exception as e:
            logger.error("Mem0 search failed: %s", e)
            return []

    async def clear(self, container_tag: str) -> None:
        """Delete all memories for a container (user)."""
        try:
            self.client.delete_all(user_id=container_tag)
        except Exception as e:
            logger.warning("Mem0 clear for %s failed: %s", container_tag, e)
