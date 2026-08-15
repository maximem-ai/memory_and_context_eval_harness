"""
Supermemory Provider — uses the official Supermemory Python SDK.

Supermemory auto-chunks content and tracks relationship types between
memories (UPDATE, EXTENDS, INFERS) with contradiction handling.
Implements the Provider interface with container_tag mapped to
Supermemory's container_tags for data isolation.
"""

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, List

from adapters.base_adapter import (
    ConcurrencyConfig,
    IndexingProgress,
    IngestOptions,
    IngestResult,
    Provider,
    ProviderConfig,
    SearchOptions,
    SessionIngestion,
    UnifiedSession,
)

logger = logging.getLogger(__name__)


class SupermemoryProvider(Provider):
    """Provider for Supermemory using the official Python SDK."""

    name = "supermemory"
    concurrency = ConcurrencyConfig(default=50, search=50, evaluate=10)

    def __init__(self):
        self.client = None
        self.top_k = 10
        self._document_ids: List[str] = []

    async def initialize(self, config: ProviderConfig) -> None:
        try:
            from supermemory import Supermemory
        except ImportError:
            raise ImportError("supermemory package is required. Install with: pip install supermemory")

        api_key = config.api_key or os.environ.get("SUPERMEMORY_API_KEY", "")
        if not api_key:
            raise ValueError("SUPERMEMORY_API_KEY is required (config or env var)")

        self.client = Supermemory(api_key=api_key)
        self.top_k = config.extras.get("top_k", 10)
        logger.info("Supermemory provider initialised (official SDK)")

    async def ingest(
        self, sessions: List[UnifiedSession], options: IngestOptions
    ) -> IngestResult:
        """Ingest sessions into Supermemory concurrently using the SDK."""
        document_ids: List[str] = []
        session_ingestions: List[SessionIngestion] = []
        CONCURRENCY = 10

        sem = asyncio.Semaphore(CONCURRENCY)

        async def _ingest_one(session: UnifiedSession):
            content = self._format_session(session)
            meta = dict(session.metadata or {})
            meta["session_id"] = session.session_id

            async with sem:
                try:
                    result = self.client.add(
                        content=content,
                        container_tags=[options.container_tag],
                        metadata=meta,
                    )
                    doc_id = getattr(result, "id", None) or str(uuid.uuid4())
                    document_ids.append(doc_id)
                    self._document_ids.append(doc_id)
                    session_ingestions.append(
                        SessionIngestion(
                            session_id=session.session_id,
                            ingestion_id=doc_id,
                            status="queued",
                            turn_count=len(session.messages),
                        )
                    )
                except Exception as e:
                    logger.error("Supermemory ingest failed for session %s: %s", session.session_id, e)
                    session_ingestions.append(
                        SessionIngestion(
                            session_id=session.session_id,
                            status="failed",
                            error=str(e),
                        )
                    )

        await asyncio.gather(*[_ingest_one(s) for s in sessions])

        return IngestResult(
            document_ids=document_ids,
            session_ingestions=session_ingestions,
        )

    async def await_indexing(
        self, result: IngestResult, container_tag: str, on_progress=None
    ) -> None:
        """Poll Supermemory until all documents are indexed."""
        if not result.document_ids:
            return

        pending = set(result.document_ids)
        completed: List[str] = []
        failed: List[str] = []
        poll_interval = 1.0
        max_interval = 5.0

        for _ in range(600):
            still_pending = set()
            for doc_id in pending:
                try:
                    doc = self.client.documents.get(doc_id)
                    status = getattr(doc, "status", "") or ""
                    if status == "done":
                        completed.append(doc_id)
                    elif status in ("failed", "error"):
                        failed.append(doc_id)
                    else:
                        still_pending.add(doc_id)
                except Exception:
                    still_pending.add(doc_id)

            if on_progress:
                on_progress(IndexingProgress(
                    completed_ids=completed,
                    failed_ids=failed,
                    total=len(result.document_ids),
                ))

            pending = still_pending
            if not pending:
                break

            await asyncio.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.2, max_interval)

    async def search(self, query: str, options: SearchOptions) -> List[Any]:
        """Search Supermemory for relevant memories using the SDK."""
        limit = options.limit or self.top_k
        try:
            result = self.client.search.documents(
                q=query,
                container_tags=[options.container_tag],
                limit=limit,
            )
            # Extract results and convert SDK objects to clean dicts
            raw_results = []
            if hasattr(result, "results"):
                raw_results = result.results
            elif isinstance(result, list):
                raw_results = result
            elif isinstance(result, dict):
                raw_results = result.get("results", [])

            return [self._normalize_search_result(r) for r in raw_results]
        except Exception as e:
            logger.error("Supermemory search failed: %s", e)
            return []

    @staticmethod
    def _normalize_search_result(result: Any) -> Dict[str, Any]:
        """Convert a Supermemory SDK search result to a clean dict."""
        # If already a dict, return as-is
        if isinstance(result, dict):
            return result

        # If it's a string (repr of SDK object), return wrapped
        if isinstance(result, str):
            return {"memory": result, "score": 0.0}

        # Extract fields from SDK object
        out: Dict[str, Any] = {}

        # Try to get the memory/summary content
        if hasattr(result, "memory"):
            out["memory"] = result.memory
        elif hasattr(result, "summary"):
            out["memory"] = result.summary
        elif hasattr(result, "content"):
            out["memory"] = result.content

        # Extract chunks content
        if hasattr(result, "chunks") and result.chunks:
            chunks_text = []
            for chunk in result.chunks:
                if hasattr(chunk, "content"):
                    chunks_text.append(chunk.content)
                elif isinstance(chunk, dict):
                    chunks_text.append(chunk.get("content", str(chunk)))
                else:
                    chunks_text.append(str(chunk))
            if chunks_text:
                out["memory"] = out.get("memory") or "\n".join(chunks_text)
                out["chunks"] = chunks_text

        # Extract score
        if hasattr(result, "score"):
            out["score"] = result.score
        elif hasattr(result, "relevance"):
            out["score"] = result.relevance

        # Extract metadata
        if hasattr(result, "metadata") and result.metadata:
            out["metadata"] = result.metadata if isinstance(result.metadata, dict) else {}
        if hasattr(result, "document_id"):
            out["document_id"] = result.document_id
        elif hasattr(result, "documentId"):
            out["document_id"] = result.documentId

        # Fallback: convert object to dict if nothing extracted
        if "memory" not in out:
            try:
                if hasattr(result, "__dict__"):
                    out = {k: v for k, v in result.__dict__.items() if not k.startswith("_")}
                elif hasattr(result, "model_dump"):
                    out = result.model_dump()
                else:
                    out["memory"] = str(result)
            except Exception:
                out["memory"] = str(result)

        return out

    async def clear(self, container_tag: str) -> None:
        """Delete all tracked documents."""
        for doc_id in self._document_ids:
            try:
                self.client.documents.delete(doc_id)
            except Exception:
                pass
        self._document_ids.clear()

    @staticmethod
    def _format_session(session: UnifiedSession) -> str:
        """Format a session into a document string for ingestion."""
        lines = []
        meta = session.metadata or {}
        if meta.get("date"):
            lines.append(f"[Date: {meta['date']}]")
        for msg in session.messages:
            speaker = msg.speaker or msg.role.capitalize()
            lines.append(f"{speaker}: {msg.content}")
        return "\n".join(lines)
