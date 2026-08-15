"""
Zep Provider — wraps the zep-cloud SDK (v3.16+).

Zep has a temporal knowledge graph (Graphiti) that auto-builds entity
relationships from conversations. Facts are extracted asynchronously
after add(). Implements the Provider interface with container_tag
mapped to Zep's user_id for data isolation.
"""

import logging
import os
import uuid
from typing import Any, List

from adapters.base_adapter import (
    ConcurrencyConfig,
    IngestOptions,
    IngestResult,
    Provider,
    ProviderConfig,
    SearchOptions,
    SessionIngestion,
    UnifiedSession,
)

logger = logging.getLogger(__name__)


class ZepProvider(Provider):
    """Provider for the Zep Cloud memory platform."""

    name = "zep"
    concurrency = ConcurrencyConfig(default=50)

    def __init__(self):
        self.client = None
        self.top_k = 10
        self._threads_created: set = set()
        self._users_created: set = set()

    def _ensure_user(self, user_id: str) -> None:
        if user_id in self._users_created:
            return
        try:
            self.client.user.add(user_id=user_id)
        except Exception:
            pass
        self._users_created.add(user_id)

    def _ensure_thread(self, thread_id: str, user_id: str) -> None:
        if thread_id in self._threads_created:
            return
        self._ensure_user(user_id)
        try:
            self.client.thread.create(thread_id=thread_id, user_id=user_id)
        except Exception:
            pass
        self._threads_created.add(thread_id)

    async def initialize(self, config: ProviderConfig) -> None:
        try:
            from zep_cloud.client import Zep
        except ImportError:
            raise ImportError(
                "zep-cloud package is required. Install with: pip install zep-cloud"
            )

        api_key = config.api_key or os.environ.get("ZEP_API_KEY", "")
        if not api_key:
            raise ValueError("ZEP_API_KEY is required (config or env var)")

        self.client = Zep(api_key=api_key)
        self.top_k = config.extras.get("top_k", 10)
        logger.info("Zep provider initialised (Zep Cloud)")

    async def ingest(
        self, sessions: List[UnifiedSession], options: IngestOptions
    ) -> IngestResult:
        """Ingest sessions into Zep. Each session becomes a thread."""
        from zep_cloud.types import Message

        document_ids: List[str] = []
        session_ingestions: List[SessionIngestion] = []

        for session in sessions:
            thread_id = session.session_id
            self._ensure_thread(thread_id, options.container_tag)

            messages = []
            for msg in session.messages:
                role = msg.role if msg.role in ("user", "assistant", "system") else "user"
                messages.append(Message(role=role, content=msg.content))

            try:
                self.client.thread.add_messages(
                    thread_id=thread_id,
                    messages=messages,
                )
                doc_id = f"zep-{thread_id}"
                document_ids.append(doc_id)
                session_ingestions.append(
                    SessionIngestion(
                        session_id=session.session_id,
                        ingestion_id=doc_id,
                        status="completed",
                        turn_count=len(session.messages),
                    )
                )
            except Exception as e:
                logger.error("Zep ingest failed for session %s: %s", session.session_id, e)
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

    async def await_indexing(
        self, result: IngestResult, container_tag: str, on_progress=None
    ) -> None:
        """Zep indexes asynchronously after thread messages are added.
        Wait a short period for graph extraction to complete."""
        import asyncio
        await asyncio.sleep(2.0)

    async def search(self, query: str, options: SearchOptions) -> List[Any]:
        """Search Zep's knowledge graph for relevant facts."""
        limit = options.limit or self.top_k
        try:
            graph_results = self.client.graph.search(
                query=query,
                user_id=options.container_tag,
                scope="edges",
                limit=limit,
            )
            edges = getattr(graph_results, "edges", None) or []
            results = []
            for edge in edges:
                results.append({
                    "id": getattr(edge, "uuid_", str(uuid.uuid4())),
                    "fact": getattr(edge, "fact", str(edge)),
                    "score": getattr(edge, "score", 0.0) or getattr(edge, "relevance", 0.5) or 0.5,
                    "valid_at": str(getattr(edge, "valid_at", "")),
                    "invalid_at": str(getattr(edge, "invalid_at", "")),
                    "source": "zep_graph",
                })
            results.sort(key=lambda x: x.get("score", 0), reverse=True)
            return results[:limit]
        except Exception as e:
            logger.error("Zep search failed: %s", e)
            return []

    async def clear(self, container_tag: str) -> None:
        """Delete all threads associated with this container (user)."""
        for thread_id in list(self._threads_created):
            try:
                self.client.thread.delete(thread_id=thread_id)
            except Exception as e:
                logger.warning("Zep clear thread %s failed: %s", thread_id, e)
        self._threads_created.clear()
        self._users_created.discard(container_tag)
