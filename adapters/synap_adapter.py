"""
Synap Provider — Real SDK integration using maximem_synap.

Uses:
  - sdk.memories.create()            for ingestion (ingest)
  - sdk.memories.batch_create()      for batch ingestion
  - sdk.memories.status()            for polling
  - sdk.user.context.fetch()         for retrieval (search)
  - sdk.cache.clear()                for clear

Requires SYNAP_API_KEY, and normally SYNAP_INSTANCE_ID and SYNAP_BASE_URL.

All methods are async — no sync-to-async bridge needed.

Retrieval and ingest are written so that a transient failure is never scored
as an empty memory: search retries and then raises SearchExhausted, a degraded
empty response is retried, and ingest records what was submitted so a resumed
run never sends a document twice.
"""

import asyncio
import logging
import re
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


class SearchExhausted(RuntimeError):
    """Retrieval failed on every attempt; the result is unknown, not empty."""


class RetrievalDegradedEmpty(RuntimeError):
    """The server returned 200 with no memories AND said a leg of the
    retrieval failed. Raised so the retry loop treats it as a failure.

    A fetch whose server-side deadline expires with nothing collected returns
    HTTP 200, an empty context, and ``retrieval: {degraded: true, level:
    "minimal", warnings: [...]}``. A retry loop that only reacts to exceptions
    would accept that 200 on the first attempt and hand the reader no context
    at all. An empty store leaves ``retrieval`` null, so the two cases are
    distinguishable and only this one is retried.
    """



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
        self._config_extras: Dict[str, Any] = {}
        self._b2c: bool = True
        self.search_retries = 4
        self.search_backoff_seconds = 3.0
        self.transient_errors: List[Dict[str, Any]] = []
        # What the server said about the most recent fetch: notably whether it
        # came from the cloud or from the SDK's on-disk cache.
        self.last_fetch_meta: Dict[str, Any] = {}
        self._indexing_poll_interval = 3.0
        self._indexing_max_interval = 15.0
        self._indexing_timeout_s = 1800.0
        self._indexing_status_concurrency = 10
        self._indexing_max_consecutive_errors = 8

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
        # Retrieval is a network call to a hosted service that can return
        # transient 5xx for a few minutes at a time and then recover. A single
        # failure must not be scored as "no memory retrieved", so search
        # retries with backoff and records every attempt.
        self.search_retries = int(config.extras.get("search_retries", 4))
        self.search_backoff_seconds = float(config.extras.get("search_backoff_seconds", 3.0))
        self.transient_errors: List[Dict[str, Any]] = []

        # Indexing poll tunables. Wall-clock is the bound, not a poll count:
        # the interval backs off, so a count does not translate to a duration.
        self._indexing_poll_interval = float(config.extras.get("indexing_poll_interval", 3.0))
        self._indexing_max_interval = float(config.extras.get("indexing_max_interval", 15.0))
        self._indexing_timeout_s = float(config.extras.get("indexing_timeout_s", 1800.0))
        self._indexing_status_concurrency = int(
            config.extras.get("indexing_status_concurrency", 10))
        self._indexing_max_consecutive_errors = int(
            config.extras.get("indexing_max_consecutive_errors", 8))

        if not self.api_key:
            raise ValueError("SYNAP_API_KEY is required (set via config or env var)")

        # The SDK's default TimeoutConfig.read is 30s. That is not enough for
        # a large batch_create payload: the upload completes but the server
        # can take longer than 30s to reply.
        #
        # This matters beyond slowness. A read timeout means the request WAS
        # delivered, so whether it was processed is unknown, and re-POSTing a
        # non-idempotent ingest can store the same memories twice. Raising the
        # ceiling is what stops that situation arising in the first place.
        from maximem_synap.models.config import TimeoutConfig
        sdk_config = SDKConfig(
            api_base_url=self.base_url or None,
            grpc_host=self.grpc_host or None,
            grpc_port=self.grpc_port or None,
            grpc_use_tls=self.grpc_use_tls,
            timeouts=TimeoutConfig(
                read=float(self._config_extras.get("http_read_timeout_s", 300.0)),
                write=float(self._config_extras.get("http_write_timeout_s", 120.0)),
            ),
        )

        sdk = MaximemSynapSDK(
            api_key=self.api_key,
            instance_id=self.instance_id or None,
            config=sdk_config,
        )

        await sdk.initialize()
        self._sdk = sdk
        await self._detect_isolation_mode()

        # The gRPC listen stream is the anticipation channel. It is useful for
        # interactive workloads and pointless for a batch benchmark, where it
        # only adds a background prefetch that competes with the retrieval
        # being measured. Default stays on; configs/synap_longmemeval_s.yaml
        # turns it off.
        if not self._config_extras.get("enable_grpc_listen", True):
            logger.info("Synap gRPC listen disabled by config (REST only)")
            logger.info("Synap provider initialised (real SDK — %s mode)", self.mode)
            return

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

    async def _detect_isolation_mode(self) -> None:
        """Ask the server whether this instance is B2C, and stop guessing.

        Sending `customer_id` alongside `user_id` is a hard 400
        (`customer_id_not_accepted_on_b2c`) on a B2C instance, and omitting it
        is a 400 on a B2B one. The mode is reported by /api/v1/auth/whoami as
        user_context_isolation, so read it rather than hardcoding either shape.
        """
        self._b2c = True
        override = self._config_extras.get("user_context_isolation")
        if override:
            self._b2c = str(override) == "equals_customer"
            logger.info("Synap isolation mode from config: %s", override)
            return
        try:
            import httpx
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(
                    f"{self.base_url.rstrip('/')}/api/v1/auth/whoami",
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "X-Instance-Id": self.instance_id})
                r.raise_for_status()
                mode = (r.json() or {}).get("user_context_isolation")
            self._b2c = (mode == "equals_customer")
            logger.info("Synap isolation mode: %s (customer_id %s)",
                        mode, "omitted" if self._b2c else "sent")
        except Exception as e:
            logger.warning("whoami failed (%s); assuming B2C and omitting customer_id", e)

    async def ingest(
        self, sessions: List[UnifiedSession], options: IngestOptions
    ) -> IngestResult:
        """Batch-ingest sessions via memories.batch_create(mode='long-range')."""
        from maximem_synap.memories.models import CreateMemoryRequest

        requests = []
        session_map: List[str] = []

        inline_ts = bool(self._config_extras.get("inline_turn_timestamps", False))

        for session in sessions:
            transcript = self._format_session(session, inline_timestamps=inline_ts)
            meta = dict(session.metadata or {})
            uid = options.container_tag
            doc_id = f"longterm:{session.session_id}"

            create_kwargs: Dict[str, Any] = dict(
                document=transcript,
                document_type="ai-chat-conversation",
                document_id=doc_id,
                user_id=uid,
                mode="long-range",
                metadata={"session_id": session.session_id, **meta},
            )

            # Document Created Time: prefer explicit document_created_at, then
            # session-level timestamp (as in the conversation datasets), then options
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

            # B2B instances require customer_id; B2C instances reject it.
            if not getattr(self, "_b2c", True):
                create_kwargs["customer_id"] = uid

            requests.append(CreateMemoryRequest(**create_kwargs))
            session_map.append(session.session_id)

        session_ingestions: List[SessionIngestion] = []
        document_ids: List[str] = []

        try:
            batch_resp = await self._batch_create_with_retry(requests)

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

    async def _batch_create_with_retry(self, requests):
        """batch_create, retried on transient 5xx.

        The SDK does not retry ingest. The hosted service can answer with a
        502 for minutes at a time, and `ingest()` turns any raised exception
        into "every session in this batch failed", so one blip would silently
        drop a whole batch and the question would be scored against a memory
        with holes in it.
        """
        from maximem_synap import (
            ServiceUnavailableError, NetworkTimeoutError,
            ConnectionError as SynapConnectionError, TransientError,
        )
        transient = (ServiceUnavailableError, NetworkTimeoutError,
                     SynapConnectionError, TransientError)

        attempts = self.search_retries + 1
        delay = self.search_backoff_seconds
        last: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                return await self._sdk.memories.batch_create(requests, fail_fast=False)
            except transient as e:
                last = e
                # A 502/503/connection-refused provably never reached the
                # handler, so re-POSTing it is safe and is the whole reason
                # this wrapper exists. A READ timeout is categorically
                # different: the request was delivered and the server may have
                # ingested it, so a retry can store every memory in the batch
                # twice -- invisibly, since nothing in the response or the
                # checkpoint would show it. The SDK draws this distinction
                # internally (outcome_unknown) and refuses the retry itself.
                #
                # The SDK does not expose outcome_unknown on the raised error,
                # so this matches on the message it constructs ("Request timed
                # out: " for a read timeout vs "Connection failed: " for a
                # connect failure). That is fragile by nature.
                if str(e).startswith("Request timed out"):
                    self.transient_errors.append({
                        "op": "batch_create", "attempt": attempt,
                        "error": type(e).__name__,
                        "detail": "read timeout, outcome unknown — NOT retried",
                    })
                    logger.error(
                        "Synap batch_create read timeout (attempt %d/%d): the request "
                        "was delivered and may have been ingested, so it will NOT be "
                        "retried -- a retry could duplicate the whole batch. Failing "
                        "this batch so the checkpoint does not record it as written.",
                        attempt, attempts)
                    raise
                self.transient_errors.append({
                    "op": "batch_create", "attempt": attempt,
                    "error": type(e).__name__, "detail": str(e)[:200],
                })
                logger.warning(
                    "Synap batch_create transient failure (attempt %d/%d): %s — "
                    "retrying in %.1fs", attempt, attempts, type(e).__name__, delay)
                if attempt == attempts:
                    break
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
        raise last  # type: ignore[misc]

    async def check_ingestion_status(self, ingestion_id: str) -> IngestionStatus:
        """Check the status of an ingestion job.

        Failures are split into two kinds, because collapsing them into one
        opaque "unknown" would leave await_indexing polling forever. A job the server
        has no record of is *terminal* — polling it again will never return
        anything else. A 5xx or a timeout is *transient* and worth retrying.
        The two are reported as distinct statuses so the caller can tell a
        finished job from a flaky network.
        """
        from uuid import UUID as _UUID
        from maximem_synap import ContextNotFoundError

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
        except ContextNotFoundError as e:
            # Terminal. `memories.status()` raises ContextNotFoundError
            # ("Ingestion job not found") for a job id the server will not
            # resolve. The extracted memories are queryable regardless, so
            # this is "done, receipt lost", not "still working".
            logger.warning("Synap ingestion job %s not found — treating as terminal",
                           ingestion_id)
            return IngestionStatus(status="unknown_completed", error_message=str(e))
        except Exception as e:
            logger.warning("Synap check_ingestion_status transient failure for %s: %s",
                           ingestion_id, e)
            return IngestionStatus(status="polling_retry", error_message=str(e))

    async def await_indexing(
        self, result: IngestResult, container_tag: str, on_progress=None
    ) -> Dict[str, Any]:
        """Poll ingestion status until every session reaches a terminal state.

        Three properties keep this loop from hanging or hiding problems:

        1. A job whose status endpoint 404s is terminal ("unknown_completed"),
           not pending. The service stops resolving a job record once it is
           done, even though the memories are already queryable.
        2. The bound is wall-clock (`indexing_timeout_s`), not a poll count,
           because the poll interval backs off.
        3. Each sweep polls the batch concurrently, since the calls are
           independent.

        Returns a summary so the caller can record what happened instead of
        discovering it in the logs.
        """
        summary: Dict[str, Any] = {
            "total": len(result.session_ingestions or []),
            "completed": 0, "failed": 0, "vanished": 0, "timed_out": 0,
            "transient_errors": 0, "polls": 0, "duration_s": 0.0,
        }
        if not result.session_ingestions:
            return summary

        pending = {
            si.ingestion_id: si
            for si in result.session_ingestions
            if si.ingestion_id and si.status in ("queued", "processing")
        }
        if not pending:
            return summary

        completed: List[str] = []
        failed: List[str] = []
        vanished: List[str] = []

        poll_interval = self._indexing_poll_interval
        min_interval = self._indexing_poll_interval
        max_interval = self._indexing_max_interval
        deadline = asyncio.get_event_loop().time() + self._indexing_timeout_s
        # A job is only abandoned after this many *consecutive* transient
        # failures, so one transient 5xx cannot strand it and a genuine
        # outage cannot spin forever.
        max_consecutive_errors = self._indexing_max_consecutive_errors
        consecutive_errors: Dict[str, int] = {}

        t0 = asyncio.get_event_loop().time()
        poll = 0
        sem = asyncio.Semaphore(self._indexing_status_concurrency)

        async def _probe(ing_id: str):
            async with sem:
                return ing_id, await self.check_ingestion_status(ing_id)

        while pending:
            now = asyncio.get_event_loop().time()
            if now >= deadline:
                summary["timed_out"] = len(pending)
                logger.error(
                    "Synap indexing timed out after %.0fs with %d/%d still pending "
                    "(%s) — continuing; their memories may still be queryable",
                    now - t0, len(pending), summary["total"],
                    ", ".join(list(pending)[:5]),
                )
                for si in pending.values():
                    si.status = "polling_retry"
                break

            poll += 1
            results = await asyncio.gather(
                *(_probe(i) for i in list(pending)), return_exceptions=True
            )

            progressed = False
            for res in results:
                if isinstance(res, BaseException):
                    summary["transient_errors"] += 1
                    continue
                ing_id, status = res
                si = pending.get(ing_id)
                if si is None:
                    continue

                if status.status in ("completed", "partial_success"):
                    completed.append(ing_id)
                    si.status = "completed"
                    si.memories_created = status.memories_created
                    si.completed_at = status.completed_at
                    pending.pop(ing_id, None)
                    progressed = True
                elif status.status == "failed":
                    failed.append(ing_id)
                    si.status = "failed"
                    si.error = status.error_message
                    pending.pop(ing_id, None)
                    progressed = True
                    logger.warning("Synap ingestion failed: %s (%s)",
                                   ing_id, status.error_message)
                elif status.status == "unknown_completed":
                    # Terminal-but-unverified. Counted separately and never
                    # folded into `completed`, so a run cannot quietly report
                    # a clean ingest that was really a pile of lost receipts.
                    vanished.append(ing_id)
                    si.status = "unknown_completed"
                    pending.pop(ing_id, None)
                    progressed = True
                elif status.status == "polling_retry":
                    summary["transient_errors"] += 1
                    self.transient_errors.append({
                        "op": "ingestion_status", "ingestion_id": ing_id,
                        "error": (status.error_message or "")[:200],
                    })
                    n = consecutive_errors.get(ing_id, 0) + 1
                    consecutive_errors[ing_id] = n
                    if n >= max_consecutive_errors:
                        logger.error(
                            "Synap ingestion %s: %d consecutive status failures — "
                            "abandoning poll (last: %s)",
                            ing_id, n, (status.error_message or "")[:120])
                        vanished.append(ing_id)
                        si.status = "unknown_completed"
                        pending.pop(ing_id, None)
                        progressed = True
                else:
                    # queued / processing — genuinely still working.
                    consecutive_errors.pop(ing_id, None)

            if on_progress:
                on_progress(IndexingProgress(
                    completed_ids=completed + vanished,
                    failed_ids=failed,
                    total=summary["total"],
                ))

            if not pending:
                break

            # Jobs in one batch land in bursts, so a sweep that saw progress is
            # a sweep worth repeating promptly. Only a quiet sweep backs off.
            if progressed:
                poll_interval = min_interval
            else:
                poll_interval = min(poll_interval * 1.2, max_interval)

            if poll % 10 == 1:
                logger.info(
                    "Synap indexing [%s]: %d completed, %d failed, %d vanished, "
                    "%d pending, %.0fs elapsed",
                    container_tag, len(completed), len(failed), len(vanished),
                    len(pending), asyncio.get_event_loop().time() - t0,
                )

            await asyncio.sleep(min(poll_interval, max(0.0, deadline - asyncio.get_event_loop().time())))

        summary.update(
            completed=len(completed), failed=len(failed), vanished=len(vanished),
            polls=poll,
            duration_s=round(asyncio.get_event_loop().time() - t0, 1),
        )
        if vanished:
            logger.warning(
                "Synap indexing [%s]: %d/%d jobs terminal-but-unverified "
                "(status endpoint would not resolve them)",
                container_tag, len(vanished), summary["total"])
        return summary

    async def search(self, query: str, options: SearchOptions) -> List[Any]:
        """User-scoped retrieval — returns raw ContextResponse items."""
        # Per-call options.limit wins, then the config default, then top_k.
        limit = options.limit or self._config_extras.get("retrieval_max_results") or self.top_k
        mode = self._config_extras.get("retrieval_mode", self.mode)

        fetch_kwargs = dict(
            user_id=options.container_tag,
            search_query=[query],
            max_results=limit,
            mode=mode,
        )
        # Log what actually goes on the wire, not what we meant to send.
        logger.debug("Synap fetch: user=%s max_results=%d mode=%s",
                     options.container_tag, limit, mode)
        retrieval_types = self._config_extras.get("retrieval_types")
        if retrieval_types:
            fetch_kwargs["types"] = retrieval_types

        last_error: Optional[Exception] = None
        for attempt in range(self.search_retries):
            try:
                context = await self._sdk.user.context.fetch(**fetch_kwargs)
                # The SDK caches fetch responses on disk with a short TTL.
                # Keep what the server says about the source, so a retrieval
                # that was replayed from that cache is distinguishable in a
                # run's record from one served live.
                md = getattr(context, "metadata", None)
                self.last_fetch_meta = {
                    "source": getattr(md, "source", None),
                    "ttl_seconds": getattr(md, "ttl_seconds", None),
                    "correlation_id": getattr(md, "correlation_id", None),
                    "retrieval_health": self._retrieval_health(context),
                }
                results = self._flatten_context(context)

                if options.threshold is not None:
                    results = [r for r in results if (r.get("score") or 0) >= options.threshold]

                self.last_fetch_meta["attempts"] = attempt + 1
                self._guard_degraded_empty(results, options.container_tag,
                                           attempt)
                return results
            except Exception as e:
                last_error = e
                self.transient_errors.append({
                    "user_id": options.container_tag,
                    "query": query[:200],
                    "attempt": attempt,
                    "error_type": type(e).__name__,
                    "error": str(e)[:400],
                    "at": datetime.now().isoformat(),
                })
                logger.warning(
                    "Synap search attempt %d/%d failed for %s: %s: %s",
                    attempt + 1, self.search_retries, options.container_tag,
                    type(e).__name__, str(e)[:200],
                )
                if attempt < self.search_retries - 1:
                    await asyncio.sleep(self.search_backoff_seconds * (2 ** attempt))

        logger.error("Synap search exhausted %d attempts: %s", self.search_retries, last_error)
        # Never return [] here. An exhausted retrieval and a genuinely empty
        # memory are different facts, and collapsing them into the same value
        # silently fabricates benchmark misses: the reader is handed no
        # context, answers wrongly, and the judge records a real failure that
        # never happened. Callers catch this and mark the question errored,
        # which excludes it from the accuracy denominator instead of counting
        # it against the memory system.
        raise SearchExhausted(
            f"search exhausted {self.search_retries} attempts for "
            f"{options.container_tag}: {type(last_error).__name__}: "
            f"{str(last_error)[:200]}"
        ) from last_error

    async def clear(self, container_tag: str) -> None:
        """Clear local cache."""
        try:
            self._sdk.cache.clear()
        except Exception as e:
            logger.warning("Synap cache clear failed: %s", e)
        self._tracked_sessions.clear()

    @classmethod
    def _format_session(cls, session: UnifiedSession, inline_timestamps: bool = False) -> str:
        """Format a UnifiedSession into a transcript string for ingestion.

        With ``inline_timestamps`` each turn is prefixed with its own
        timestamp. This matters whenever one ingested document spans turns
        from more than one day: without the prefixes every extracted event
        inherits the document's created-at date.

        Off by default, and off in configs/synap_longmemeval_s.yaml: the S
        split supplies only a session-level date, so every turn in a document
        would carry the same prefix.
        """
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

            prefix = ""
            if inline_timestamps and msg.timestamp:
                stamp = cls._format_turn_stamp(msg.timestamp)
                if stamp:
                    prefix = f"[{stamp}] "
            lines.append(f"{prefix}{label}: {msg.content}")
        return "\n".join(lines)

    @classmethod
    def _format_turn_stamp(cls, value: Any) -> str:
        """Render a turn timestamp as 'YYYY-MM-DD HH:MM', or '' if unparseable.

        Falls back to the raw string rather than dropping it: an unparseable
        but present timestamp still carries ordering information the
        extractor can use.
        """
        dt = cls._parse_dct(value)
        if dt is not None:
            return dt.strftime("%Y-%m-%d %H:%M")
        raw = str(value).strip()
        return raw if raw else ""

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
        # Some datasets use '1:56 pm on 8 May, 2023' — dateutil handles 'on'
        s = s.replace(" on ", " ")
        try:
            from dateutil import parser as date_parser
            return date_parser.parse(s)
        except (ValueError, TypeError) as e:
            logger.warning("Synap DCT parse failed for %r: %s", value, e)
            return None

    @staticmethod
    def _retrieval_health(source) -> Optional[Dict[str, Any]]:
        """The server's ``retrieval`` block, from a raw payload or an SDK model.

        ``ContextFetchResponse.retrieval`` is null on a healthy fetch, so a
        missing field means healthy and not "old server". The SDK's
        ContextResponse allows extra fields and keeps the whole body on
        ``.raw``, so the field survives even though the typed model has no
        slot for it.
        """
        if isinstance(source, dict):
            health = source.get("retrieval")
        else:
            health = getattr(source, "retrieval", None)
            if health is None:
                health = (getattr(source, "raw", None) or {}).get("retrieval")
        if health is None:
            return None
        if not isinstance(health, dict):
            health = {k: getattr(health, k, None)
                      for k in ("degraded", "level", "warnings")}
        return health

    def _guard_degraded_empty(self, results: List[Dict[str, Any]],
                              container_tag: str, attempt: int) -> None:
        """Raise if the server returned nothing AND said a leg failed.

        Only this combination is a failure. Zero items with no ``retrieval``
        block is a genuinely empty store, and zero items with
        ``degraded: false`` is the search-query cap, which is not a failed leg.
        """
        if results:
            return
        health = (self.last_fetch_meta or {}).get("retrieval_health") or {}
        if not health or not health.get("degraded", True):
            return
        warnings = health.get("warnings") or []
        logger.warning(
            "Synap fetch returned 0 items and degraded=%s level=%s for %s "
            "(attempt %d/%d): %s",
            health.get("degraded"), health.get("level"), container_tag,
            attempt + 1, self.search_retries, "; ".join(map(str, warnings))[:300],
        )
        raise RetrievalDegradedEmpty(
            f"degraded retrieval returned 0 items for {container_tag}: "
            f"level={health.get('level')} warnings={warnings}"
        )

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
                "said_at": str(getattr(fact, "said_at", None)) if getattr(fact, "said_at", None) else None,
                "event_end": str(getattr(fact, "event_end", None)) if getattr(fact, "event_end", None) else None,
                "event_precision": getattr(fact, "event_precision", None),
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
                "said_at": str(getattr(pref, "said_at", None)) if getattr(pref, "said_at", None) else None,
                "event_end": str(getattr(pref, "event_end", None)) if getattr(pref, "event_end", None) else None,
                "event_precision": getattr(pref, "event_precision", None),
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
                "said_at": str(getattr(ep, "said_at", None)) if getattr(ep, "said_at", None) else None,
                "event_end": str(getattr(ep, "event_end", None)) if getattr(ep, "event_end", None) else None,
                "event_precision": getattr(ep, "event_precision", None),
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
                "said_at": str(getattr(emo, "said_at", None)) if getattr(emo, "said_at", None) else None,
                "event_end": str(getattr(emo, "event_end", None)) if getattr(emo, "event_end", None) else None,
                "event_precision": getattr(emo, "event_precision", None),
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
                "said_at": str(getattr(te, "said_at", None)) if getattr(te, "said_at", None) else None,
                "event_end": str(getattr(te, "event_end", None)) if getattr(te, "event_end", None) else None,
                "event_precision": getattr(te, "event_precision", None),
                "valid_until": str(te.valid_until) if getattr(te, "valid_until", None) else None,
                "temporal_category": getattr(te, "temporal_category", None),
                "temporal_confidence": getattr(te, "temporal_confidence", 0.0),
                "extracted_at": str(te.extracted_at) if getattr(te, "extracted_at", None) else None,
                "source_evidence": getattr(te, "source_evidence", []),
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results
