"""
Synap Provider — Real SDK integration using maximem_synap.

Uses:
  - sdk.memories.create()            for ingestion (ingest)
  - sdk.memories.batch_create()      for batch ingestion
  - sdk.memories.status()            for polling
  - sdk.user.context.fetch()         for retrieval (search)
  - sdk.memories.delete()            for deletion
  - sdk.cache.clear()                for clear

Requires env vars: SYNAP_API_KEY, and optionally SYNAP_INSTANCE_ID, SYNAP_BASE_URL.

All methods are async — no _AsyncBridge needed.
"""

import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from adapters.base_adapter import (
    ConcurrencyConfig,
    IndexingProgress,
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


class SynapProvider(Provider):
    """Provider backed by the maximem_synap SDK."""

    name = "synap"
    concurrency = ConcurrencyConfig(default=5, search=10, answer=10, evaluate=10)

    def __init__(self):
        self._sdk = None
        self.api_key = ""
        self.instance_id = ""
        self.base_url = ""
        self.grpc_host = ""
        self.grpc_port = 0
        self.grpc_use_tls = True
        self.mode = "accurate"
        self.top_k = 10
        self._tracked_sessions: set = set()
        self._session_uuid_ns = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        self._config_extras: Dict[str, Any] = {}

    @staticmethod
    def _to_uuid(value: str, namespace: uuid.UUID) -> str:
        """Convert an arbitrary string to a deterministic UUID v5."""
        return str(uuid.uuid5(namespace, value))

    async def initialize(self, config: ProviderConfig) -> None:
        from maximem_synap import MaximemSynapSDK, SDKConfig

        self.api_key = config.api_key or config.extras.get("api_key", "")
        self.instance_id = config.extras.get("instance_id", "")
        self.base_url = config.base_url or config.extras.get("base_url", "")
        self.grpc_host = config.extras.get("grpc_host", "")
        self.grpc_port = config.extras.get("grpc_port", 0)
        self.grpc_use_tls = config.extras.get("grpc_use_tls", True)
        self.mode = config.extras.get("mode", "accurate").lower()
        self.top_k = config.extras.get("top_k", 10)
        self._config_extras = config.extras

        if not self.api_key:
            raise ValueError("SYNAP_API_KEY is required (set via config or env var)")

        sdk_config = SDKConfig(
            api_base_url=self.base_url or None,
            grpc_host=self.grpc_host or None,
            grpc_port=self.grpc_port or None,
            grpc_use_tls=self.grpc_use_tls,
        )

        sdk = MaximemSynapSDK(
            api_key=self.api_key,
            instance_id=self.instance_id or None,
            config=sdk_config,
        )

        await sdk.initialize()
        self._sdk = sdk

        # Start gRPC listen stream
        try:
            await sdk.instance.listen(
                on_reconnect=lambda attempt: logger.info("Synap gRPC reconnected (attempt %d)", attempt),
                on_disconnect=lambda reason: logger.warning("Synap gRPC disconnected: %s", reason),
                on_context=lambda bundle: logger.debug("Synap anticipated context bundle received"),
            )
            logger.info("Synap gRPC listen stream established")
        except Exception as e:
            logger.warning("Synap gRPC listen failed (falling back to REST): %s", e)

        logger.info("Synap provider initialised (real SDK — %s mode, TLS=%s)", self.mode, self.grpc_use_tls)

    async def ingest(
        self, sessions: List[UnifiedSession], options: IngestOptions
    ) -> IngestResult:
        """Batch-ingest sessions via memories.batch_create(mode='long-range')."""
        from maximem_synap.memories.models import CreateMemoryRequest

        requests = []
        session_map: List[str] = []

        for session in sessions:
            transcript = self._format_session(session)
            meta = dict(session.metadata or {})
            uid = options.container_tag
            doc_id = f"longterm:{session.session_id}"

            create_kwargs: Dict[str, Any] = dict(
                document=transcript,
                document_type="ai-chat-conversation",
                document_id=doc_id,
                user_id=uid,
                # Pydantic-required on the SDK model. For B2C instances the
                # server auto-resolves from user_id, so the value sent is
                # effectively ignored — passing user_id keeps it stable.
                customer_id=uid,
                mode="long-range",
                metadata={"session_id": session.session_id, **meta},
            )

            # Document Created Time: prefer explicit document_created_at, then
            # session-level timestamp (Locomo, LongMemEval), then options
            # override. Parse human-readable strings to datetime.
            dct_raw = (
                (options.metadata or {}).get("document_created_at")
                or meta.get("document_created_at")
                or meta.get("timestamp")
                or meta.get("session_date")
            )
            dct = self._parse_dct(dct_raw) if dct_raw else None
            if dct is not None:
                create_kwargs["document_created_at"] = dct

            requests.append(CreateMemoryRequest(**create_kwargs))
            session_map.append(session.session_id)

        session_ingestions: List[SessionIngestion] = []
        document_ids: List[str] = []

        try:
            batch_resp = await self._sdk.memories.batch_create(requests, fail_fast=False)

            for i, session_id in enumerate(session_map):
                r = batch_resp.results[i] if i < len(batch_resp.results) else None
                ingestion_id = str(r.ingestion_id) if r else None
                status = r.status.value if r else "failed"
                error = r.error_message if r and r.error_message else None

                if ingestion_id:
                    document_ids.append(ingestion_id)
                session_ingestions.append(
                    SessionIngestion(
                        session_id=session_id,
                        ingestion_id=ingestion_id,
                        status="queued" if status in ("queued", "accepted") else status,
                        turn_count=len(sessions[i].messages) if i < len(sessions) else 0,
                        batch_id=str(batch_resp.batch_id),
                        error=error,
                    )
                )
                self._tracked_sessions.add(session_id)

            logger.info(
                "Synap batch ingest: %d sessions batch_id=%s succeeded=%d failed=%d",
                len(sessions), batch_resp.batch_id, batch_resp.succeeded, batch_resp.failed,
            )
        except Exception as e:
            logger.error("Synap batch ingest failed: %s", e)
            for session_id in session_map:
                session_ingestions.append(
                    SessionIngestion(
                        session_id=session_id,
                        status="failed",
                        error=str(e),
                    )
                )
                self._tracked_sessions.add(session_id)

        return IngestResult(
            document_ids=document_ids,
            session_ingestions=session_ingestions,
        )

    async def ingest_fire_and_forget(
        self, sessions: List[UnifiedSession], options: IngestOptions
    ) -> IngestResult:
        """Same as ingest() — batch_create already returns immediately."""
        return await self.ingest(sessions, options)

    async def check_ingestion_status(self, ingestion_id: str) -> IngestionStatus:
        """Check the status of an ingestion job."""
        from uuid import UUID as _UUID

        try:
            resp = await self._sdk.memories.status(_UUID(ingestion_id))
            return IngestionStatus(
                status=resp.status.value,
                memories_created=resp.memories_created,
                memory_ids=resp.memory_ids,
                error_message=resp.error_message,
                started_at=str(resp.started_at) if resp.started_at else None,
                completed_at=str(resp.completed_at) if resp.completed_at else None,
            )
        except Exception as e:
            logger.error("Synap check_ingestion_status failed for %s: %s", ingestion_id, e)
            return IngestionStatus(status="unknown", error_message=str(e))

    async def await_indexing(
        self, result: IngestResult, container_tag: str, on_progress=None
    ) -> None:
        """Poll ingestion status until all sessions are completed."""
        if not result.session_ingestions:
            return

        pending = {
            si.ingestion_id: si.session_id
            for si in result.session_ingestions
            if si.ingestion_id and si.status in ("queued", "processing")
        }
        if not pending:
            return

        completed: List[str] = []
        failed: List[str] = []
        poll_interval = 3.0
        max_interval = 30.0
        max_polls = 600  # ~30 min

        for poll in range(max_polls):
            still_pending = {}
            for ing_id, session_id in pending.items():
                status = await self.check_ingestion_status(ing_id)
                if status.status in ("completed", "partial_success"):
                    completed.append(ing_id)
                elif status.status == "failed":
                    failed.append(ing_id)
                    logger.warning("Synap ingestion failed: %s (%s)", ing_id, status.error_message)
                else:
                    still_pending[ing_id] = session_id

            if on_progress:
                on_progress(IndexingProgress(
                    completed_ids=completed,
                    failed_ids=failed,
                    total=len(result.session_ingestions),
                ))

            pending = still_pending
            if not pending:
                break

            await asyncio.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.2, max_interval)

            if poll % 5 == 0:
                logger.info(
                    "Synap indexing: %d completed, %d failed, %d pending",
                    len(completed), len(failed), len(pending),
                )

    async def search(self, query: str, options: SearchOptions) -> List[Any]:
        """User-scoped retrieval — returns raw ContextResponse items."""
        limit = self._config_extras.get("retrieval_max_results", options.limit or self.top_k)
        mode = self._config_extras.get("retrieval_mode", self.mode)

        try:
            fetch_kwargs = dict(
                user_id=options.container_tag,
                search_query=[query],
                max_results=limit,
                mode=mode,
            )
            retrieval_types = self._config_extras.get("retrieval_types")
            if retrieval_types:
                fetch_kwargs["types"] = retrieval_types

            context = await self._sdk.user.context.fetch(**fetch_kwargs)
            results = self._flatten_context(context)

            if options.threshold is not None:
                results = [r for r in results if (r.get("score") or 0) >= options.threshold]

            return results
        except Exception as e:
            logger.error("Synap search failed: %s", e)
            return []

    async def clear(self, container_tag: str) -> None:
        """Clear local cache."""
        try:
            self._sdk.cache.clear()
        except Exception as e:
            logger.warning("Synap cache clear failed: %s", e)
        self._tracked_sessions.clear()

    @staticmethod
    def _format_session(session: UnifiedSession) -> str:
        """Format a UnifiedSession into a transcript string for ingestion."""
        lines = []
        for msg in session.messages:
            raw_speaker = msg.speaker or ""
            # Skip generic "speaker_a"/"speaker_b" placeholders — they are
            # not informative and confuse entity extraction. Use real names
            # when present, otherwise fall back to role.
            if raw_speaker and not raw_speaker.startswith("speaker_"):
                label = raw_speaker
            else:
                label = msg.role.capitalize()
            lines.append(f"{label}: {msg.content}")
        return "\n".join(lines)

    _DOW_PAREN_RE = re.compile(r"\([A-Za-z]{3,9}\)")

    @classmethod
    def _parse_dct(cls, value: Any) -> Optional[datetime]:
        """Parse a Document Created Time from various formats:
        - datetime: returned as-is
        - ISO strings: 2023-05-08T13:56:00, 2023/05/25 (Thu) 20:21
        - Human strings: '1:56 pm on 8 May, 2023'
        """
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value
        s = str(value).strip()
        # LongMemEval uses '2023/05/25 (Thu) 20:21' — strip the day-of-week
        s = cls._DOW_PAREN_RE.sub("", s).strip()
        # Locomo uses '1:56 pm on 8 May, 2023' — dateutil handles 'on'
        s = s.replace(" on ", " ")
        try:
            from dateutil import parser as date_parser
            return date_parser.parse(s)
        except (ValueError, TypeError) as e:
            logger.warning("Synap DCT parse failed for %r: %s", value, e)
            return None

    @staticmethod
    def _flatten_context(context) -> List[Dict[str, Any]]:
        """Convert a ContextResponse into a flat list of items with scores."""
        results: List[Dict[str, Any]] = []

        for fact in getattr(context, "facts", []):
            results.append({
                "id": str(fact.id),
                "memory": fact.content,
                "score": fact.confidence,
                "source": getattr(fact, "source", ""),
                "metadata": getattr(fact, "metadata", {}),
                "context_type": "fact",
                "event_date": str(fact.event_date) if getattr(fact, "event_date", None) else None,
                "valid_until": str(fact.valid_until) if getattr(fact, "valid_until", None) else None,
                "temporal_category": getattr(fact, "temporal_category", None),
                "temporal_confidence": getattr(fact, "temporal_confidence", 0.0),
                "extracted_at": str(fact.extracted_at) if getattr(fact, "extracted_at", None) else None,
                "source_evidence": getattr(fact, "source_evidence", []),
            })

        for pref in getattr(context, "preferences", []):
            results.append({
                "id": str(pref.id),
                "memory": pref.content,
                "score": pref.strength,
                "source": getattr(pref, "source", ""),
                "metadata": getattr(pref, "metadata", {}),
                "context_type": "preference",
                "event_date": str(pref.event_date) if getattr(pref, "event_date", None) else None,
                "valid_until": str(pref.valid_until) if getattr(pref, "valid_until", None) else None,
                "temporal_category": getattr(pref, "temporal_category", None),
                "temporal_confidence": getattr(pref, "temporal_confidence", 0.0),
                "category": getattr(pref, "category", ""),
                "extracted_at": str(pref.extracted_at) if getattr(pref, "extracted_at", None) else None,
                "source_evidence": getattr(pref, "source_evidence", []),
            })

        for ep in getattr(context, "episodes", []):
            results.append({
                "id": str(ep.id),
                "memory": ep.summary,
                "score": ep.significance,
                "metadata": getattr(ep, "metadata", {}),
                "context_type": "episode",
                "event_date": str(ep.event_date) if getattr(ep, "event_date", None) else None,
                "valid_until": str(ep.valid_until) if getattr(ep, "valid_until", None) else None,
                "temporal_category": getattr(ep, "temporal_category", None),
                "temporal_confidence": getattr(ep, "temporal_confidence", 0.0),
                "occurred_at": str(ep.occurred_at) if getattr(ep, "occurred_at", None) else None,
                "participants": getattr(ep, "participants", []),
                "source_evidence": getattr(ep, "source_evidence", []),
            })

        for emo in getattr(context, "emotions", []):
            results.append({
                "id": str(emo.id),
                "memory": emo.context,
                "score": emo.intensity,
                "metadata": getattr(emo, "metadata", {}),
                "context_type": "emotion",
                "event_date": str(emo.event_date) if getattr(emo, "event_date", None) else None,
                "valid_until": str(emo.valid_until) if getattr(emo, "valid_until", None) else None,
                "temporal_category": getattr(emo, "temporal_category", None),
                "temporal_confidence": getattr(emo, "temporal_confidence", 0.0),
                "emotion_type": getattr(emo, "emotion_type", ""),
                "detected_at": str(emo.detected_at) if getattr(emo, "detected_at", None) else None,
                "source_evidence": getattr(emo, "source_evidence", []),
            })

        for te in getattr(context, "temporal_events", []):
            results.append({
                "id": str(te.id),
                "memory": getattr(te, "content", ""),
                "score": getattr(te, "confidence", 0.0),
                "source": getattr(te, "source", ""),
                "metadata": getattr(te, "metadata", {}),
                "context_type": "temporal_event",
                "event_date": str(te.event_date) if getattr(te, "event_date", None) else None,
                "valid_until": str(te.valid_until) if getattr(te, "valid_until", None) else None,
                "temporal_category": getattr(te, "temporal_category", None),
                "temporal_confidence": getattr(te, "temporal_confidence", 0.0),
                "extracted_at": str(te.extracted_at) if getattr(te, "extracted_at", None) else None,
                "source_evidence": getattr(te, "source_evidence", []),
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results
