"""
Mem0 Provider — async HTTP client supporting OSS and cloud modes.

Two backend modes:
  - oss (default): Self-hosted Mem0 OSS server at MEM0_HOST (default http://localhost:8888).
    Synchronous REST endpoints, no auth required.
  - cloud: Mem0 cloud API (api.mem0.ai). V3 endpoints with async event polling.
    Requires MEM0_API_KEY + optional MEM0_ORG_ID / MEM0_PROJECT_ID.

Both modes expose the same async interface via Mem0Provider.
"""

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

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


# ---------------------------------------------------------------------------
# Async Mem0 HTTP client (mirrors memory-benchmarks Mem0Client)
# ---------------------------------------------------------------------------


class Mem0Client:
    """Async Mem0 client supporting both OSS server and cloud API.

    Args:
        mode: "oss" for self-hosted server, "cloud" for api.mem0.ai.
        host: Server URL. Defaults to MEM0_HOST env or http://localhost:8888 (oss)
              / https://api.mem0.ai (cloud).
        api_key: Cloud API key. Falls back to MEM0_API_KEY env var.
        organization_id: Cloud org ID. Falls back to MEM0_ORGANIZATION_ID.
        project_id: Cloud project ID. Falls back to MEM0_PROJECT_ID.
        max_retries: Maximum retry attempts for API calls.
        retry_delay: Base delay in seconds between retries (doubles each attempt).
        timeout: HTTP request timeout in seconds.
        event_poll_interval: Seconds between event status polls (cloud only).
        event_poll_timeout: Max seconds to wait for event completion (cloud only).
    """

    def __init__(
        self,
        mode: str = "oss",
        host: str | None = None,
        api_key: str | None = None,
        organization_id: str | None = None,
        project_id: str | None = None,
        max_retries: int = 5,
        retry_delay: float = 5.0,
        timeout: float = 300.0,
        event_poll_interval: float = 0.5,
        event_poll_timeout: float = 300.0,
    ):
        self.mode = mode

        default_host = "https://api.mem0.ai" if mode == "cloud" else "http://localhost:8888"
        self.host = (host or os.getenv("MEM0_HOST", default_host)).rstrip("/")
        self.api_key = api_key or os.getenv("MEM0_API_KEY", "")
        self.organization_id = organization_id or os.getenv("MEM0_ORGANIZATION_ID", "")
        self.project_id = project_id or os.getenv("MEM0_PROJECT_ID", "")
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.event_poll_interval = event_poll_interval
        self.event_poll_timeout = event_poll_timeout
        self._timeout = httpx.Timeout(timeout)
        self._client: httpx.AsyncClient | None = None

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.mode == "cloud" and self.api_key:
            headers["Authorization"] = f"Token {self.api_key}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self._headers,
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "Mem0Client":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # -------------------------------------------------------------------------
    # Add
    # -------------------------------------------------------------------------

    async def add(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        observation_date: str | None = None,
        timestamp: int | None = None,
        custom_instructions: str | None = None,
        metadata: dict | None = None,
    ) -> dict | None:
        """Add memories from a conversation. Returns dict with 'results' key or None on failure."""
        if self.mode == "oss":
            return await self._add_oss(messages, user_id, observation_date, timestamp, custom_instructions, metadata)
        else:
            return await self._add_cloud(messages, user_id, observation_date, timestamp, custom_instructions, metadata)

    async def _add_oss(
        self, messages, user_id, observation_date, timestamp, custom_instructions, metadata
    ) -> dict | None:
        client = await self._get_client()
        payload: dict[str, Any] = {"messages": messages, "user_id": user_id}
        if timestamp is not None:
            payload["timestamp"] = timestamp
        elif observation_date is not None:
            try:
                d = datetime.strptime(observation_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                payload["timestamp"] = int(d.timestamp())
            except ValueError:
                pass
        if custom_instructions:
            payload["custom_instructions"] = custom_instructions
        if metadata:
            payload["metadata"] = metadata

        for attempt in range(self.max_retries):
            try:
                resp = await client.post(f"{self.host}/memories", json=payload)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("Server error", request=resp.request, response=resp)
                resp.raise_for_status()
                data = resp.json()

                # Normalise: OSS returns {"results": [...]} directly
                if isinstance(data, dict) and "results" in data:
                    return data
                if isinstance(data, list):
                    return {"results": data}
                return {"results": []}

            except Exception as exc:
                logger.warning("ADD attempt %d/%d failed (user=%s): %s", attempt + 1, self.max_retries, user_id, str(exc)[:200])
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                else:
                    logger.error("ADD failed after %d attempts for user=%s", self.max_retries, user_id)
                    return None

    async def _add_cloud(
        self, messages, user_id, observation_date, timestamp, custom_instructions, metadata
    ) -> dict | None:
        client = await self._get_client()
        payload: dict[str, Any] = {"messages": messages, "user_id": user_id}
        if timestamp is not None:
            payload["timestamp"] = timestamp
        elif observation_date is not None:
            try:
                d = datetime.strptime(observation_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                payload["timestamp"] = int(d.timestamp())
            except ValueError:
                pass
        if custom_instructions:
            payload["custom_instructions"] = custom_instructions
        if metadata:
            payload["metadata"] = metadata

        for attempt in range(self.max_retries):
            try:
                resp = await client.post(f"{self.host}/v3/memories/add/", json=payload)
                resp.raise_for_status()
                resp_data = resp.json()

                event_id = resp_data.get("event_id")
                if not event_id:
                    logger.warning("V3 add returned no event_id: %s", resp_data)
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(self.retry_delay * (attempt + 1))
                        continue
                    return None

                event_data = await self._wait_for_event(event_id)
                if event_data is None:
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(self.retry_delay * (attempt + 1))
                        continue
                    return None

                return {"results": self._parse_event_results(event_data)}

            except Exception as exc:
                logger.warning("ADD attempt %d/%d failed (user=%s): %s", attempt + 1, self.max_retries, user_id, str(exc)[:200])
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                else:
                    logger.error("ADD failed after %d attempts for user=%s", self.max_retries, user_id)
                    return None

    # -------------------------------------------------------------------------
    # Search
    # -------------------------------------------------------------------------

    async def search(
        self,
        query: str,
        user_id: str,
        top_k: int = 200,
        rerank: bool = False,
        score_debug: bool = False,
    ) -> list[dict]:
        """Search memories. Returns list of results sorted by score descending."""
        if self.mode == "oss":
            return await self._search_oss(query, user_id, top_k, rerank)
        else:
            return await self._search_cloud(query, user_id, top_k, rerank, score_debug)

    async def _search_oss(self, query, user_id, top_k, rerank) -> list[dict]:
        client = await self._get_client()
        payload: dict[str, Any] = {"query": query, "user_id": user_id, "limit": top_k}
        if rerank:
            payload["rerank"] = True

        for attempt in range(self.max_retries):
            try:
                resp = await client.post(f"{self.host}/search", json=payload)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("Server error", request=resp.request, response=resp)
                resp.raise_for_status()
                data = resp.json()

                results = data.get("results", data) if isinstance(data, dict) else data
                if not isinstance(results, list):
                    results = []

                normalised = []
                for r in results:
                    entry: dict[str, Any] = {
                        "memory": r.get("memory", r.get("data", "")),
                        "score": r.get("score", 0),
                        "id": r.get("id", ""),
                    }
                    if r.get("created_at"):
                        entry["created_at"] = r["created_at"]
                    if r.get("updated_at"):
                        entry["updated_at"] = r["updated_at"]
                    breakdown = r.get("score_breakdown") or r.get("score_debug")
                    if breakdown:
                        entry["score_debug"] = {
                            "combined_score": r.get("score", 0),
                            "semantic_score": breakdown.get("semantic", 0),
                            "bm25_score": breakdown.get("bm25", 0),
                            "entity_boost": breakdown.get("entity_boost", 0),
                        }
                    normalised.append(entry)

                normalised.sort(key=lambda x: x.get("score", 0), reverse=True)
                return normalised

            except Exception as exc:
                logger.warning("SEARCH attempt %d/%d failed (user=%s): %s", attempt + 1, self.max_retries, user_id, str(exc)[:200])
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                else:
                    logger.error("SEARCH failed after %d attempts for user=%s", self.max_retries, user_id)
                    return []

    async def _search_cloud(self, query, user_id, top_k, rerank, score_debug) -> list[dict]:
        client = await self._get_client()
        payload: dict[str, Any] = {
            "query": query,
            "filters": {"user_id": user_id},
            "top_k": top_k,
            "rerank": rerank,
        }
        if score_debug:
            payload["score_debug"] = True

        for attempt in range(self.max_retries):
            try:
                resp = await client.post(f"{self.host}/v3/memories/search/", json=payload)
                resp.raise_for_status()
                resp_data = resp.json()

                results = resp_data.get("results", []) if isinstance(resp_data, dict) else resp_data
                results.sort(key=lambda x: x.get("score", 0), reverse=True)
                return results

            except Exception as exc:
                logger.warning("SEARCH attempt %d/%d failed (user=%s): %s", attempt + 1, self.max_retries, user_id, str(exc)[:200])
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                else:
                    logger.error("SEARCH failed after %d attempts for user=%s", self.max_retries, user_id)
                    return []

    # -------------------------------------------------------------------------
    # Delete
    # -------------------------------------------------------------------------

    async def delete_user(self, user_id: str) -> bool:
        """Delete all memories for a user. Returns True on success."""
        if self.mode == "oss":
            return await self._delete_user_oss(user_id)
        else:
            return await self._delete_user_cloud(user_id)

    async def _delete_user_oss(self, user_id: str) -> bool:
        client = await self._get_client()
        try:
            resp = await client.request("DELETE", f"{self.host}/memories", params={"user_id": user_id})
            resp.raise_for_status()
            logger.info("Deleted memories for user %s", user_id)
            return True
        except Exception as exc:
            logger.warning("Failed to delete user %s: %s", user_id, exc)
            return False

    async def _delete_user_cloud(self, user_id: str) -> bool:
        client = await self._get_client()
        try:
            resp = await client.delete(f"{self.host}/v1/entities/user/{user_id}/")
            resp.raise_for_status()
            logger.info("Deleted user %s", user_id)
            return True
        except Exception as exc:
            logger.warning("Failed to delete user %s: %s", user_id, exc)
            return False

    # -------------------------------------------------------------------------
    # Cloud-only: event polling
    # -------------------------------------------------------------------------

    async def _get_event_status(self, event_id: str) -> dict | None:
        client = await self._get_client()
        url = f"{self.host}/v1/event/{event_id}/"
        for attempt in range(3):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.warning("Event poll %d/3 failed for %s: %s", attempt + 1, event_id, exc)
                if attempt < 2:
                    await asyncio.sleep(self.retry_delay)
        return None

    async def _wait_for_event(self, event_id: str) -> dict | None:
        start = time.monotonic()
        while (time.monotonic() - start) < self.event_poll_timeout:
            data = await self._get_event_status(event_id)
            if data is None:
                return None
            status = data.get("status", "UNKNOWN")
            if status == "SUCCEEDED":
                return data
            if status == "FAILED":
                logger.error("Event %s failed: %s", event_id, data.get("error", ""))
                return None
            await asyncio.sleep(self.event_poll_interval)
        logger.error("Event %s timed out after %.0fs", event_id, self.event_poll_timeout)
        return None

    @staticmethod
    def _parse_event_results(event_data: dict) -> list[dict]:
        raw = event_data.get("results", [])
        results = []
        if not raw or not isinstance(raw, list):
            return results
        for item in raw:
            if not isinstance(item, dict):
                continue
            if "memory" in item and "event" in item:
                results.append(item)
            elif "data" in item and isinstance(item.get("data"), dict):
                data = item["data"]
                entry = {
                    "id": item.get("id"),
                    "event": item.get("event"),
                    "memory": data.get("memory"),
                }
                if item.get("event") == "UPDATE":
                    entry["previous_memory"] = data.get("old_memory", data.get("previous_memory", ""))
                results.append(entry)
        return results


# ---------------------------------------------------------------------------
# Provider wrapper
# ---------------------------------------------------------------------------


class Mem0Provider(Provider):
    """Provider for Mem0 — wraps Mem0Client with OSS and cloud support."""

    name = "mem0"
    concurrency = ConcurrencyConfig(default=50)

    def __init__(self):
        self.client: Mem0Client | None = None
        self.top_k = 10

    async def initialize(self, config: ProviderConfig) -> None:
        api_key = config.api_key or os.getenv("MEM0_API_KEY", "")
        org_id = config.extras.get("org_id") or os.getenv("MEM0_ORGANIZATION_ID", "")
        project_id = config.extras.get("project_id") or os.getenv("MEM0_PROJECT_ID", "")

        # Infer mode: explicit > env > presence of api_key
        mode = config.extras.get("mode") or os.getenv("MEM0_MODE") or ("cloud" if api_key else "oss")
        # host: explicit extras > base_url origin (strip path) > env
        raw_host = config.extras.get("host") or config.base_url or os.getenv("MEM0_HOST")
        if raw_host:
            from urllib.parse import urlparse
            p = urlparse(raw_host)
            host = f"{p.scheme}://{p.netloc}" if p.netloc else raw_host
        else:
            host = None

        if mode == "cloud" and not api_key:
            raise ValueError("MEM0_API_KEY is required for cloud mode")

        self.client = Mem0Client(
            mode=mode,
            host=host,
            api_key=api_key,
            organization_id=org_id,
            project_id=project_id,
        )
        self.top_k = config.extras.get("top_k", 10)
        logger.info("Mem0 provider initialised (mode=%s, host=%s)", mode, self.client.host)

    async def ingest(self, sessions: List[UnifiedSession], options: IngestOptions) -> IngestResult:
        document_ids: List[str] = []
        session_ingestions: List[SessionIngestion] = []

        for session in sessions:
            messages = [{"role": msg.role, "content": msg.content} for msg in session.messages]
            meta = dict(session.metadata or {})
            meta["session_id"] = session.session_id

            observation_date = meta.get("observation_date") or meta.get("date")

            try:
                result = await self.client.add(
                    messages=messages,
                    user_id=options.container_tag,
                    observation_date=observation_date,
                    metadata=meta,
                )
                memories_created = 0
                if result and isinstance(result, dict):
                    for r in result.get("results", []):
                        doc_id = r.get("id") or str(uuid.uuid4())
                        document_ids.append(doc_id)
                        memories_created += 1

                session_ingestions.append(
                    SessionIngestion(
                        session_id=session.session_id,
                        status="completed",
                        turn_count=len(session.messages),
                        memories_created=memories_created,
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

        return IngestResult(document_ids=document_ids, session_ingestions=session_ingestions)

    async def await_indexing(self, result: IngestResult, container_tag: str, on_progress=None) -> None:
        pass

    async def search(self, query: str, options: SearchOptions) -> List[Any]:
        limit = options.limit or self.top_k
        try:
            return await self.client.search(
                query=query,
                user_id=options.container_tag,
                top_k=limit,
            )
        except Exception as e:
            logger.error("Mem0 search failed: %s", e)
            return []

    async def clear(self, container_tag: str) -> None:
        try:
            await self.client.delete_user(container_tag)
        except Exception as e:
            logger.warning("Mem0 clear for %s failed: %s", container_tag, e)
