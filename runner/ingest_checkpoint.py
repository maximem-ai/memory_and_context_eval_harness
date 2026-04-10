"""
Global ingest checkpoint manager — tracks ingestion state per provider+benchmark.

Stores checkpoints at: data/ingest-checkpoints/{dataset}.json
Decoupled from eval runs: ingest once, run many evals.
"""

import asyncio
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from runner.types import (
    GlobalIngestCheckpoint,
    ProviderIngestState,
    SessionIngestionRecord,
)

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
INGEST_DIR = os.path.join(DATA_DIR, "ingest-checkpoints")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


class GlobalIngestCheckpointManager:
    """Manages global ingest checkpoints with atomic file writes."""

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, dataset: str) -> asyncio.Lock:
        if dataset not in self._locks:
            self._locks[dataset] = asyncio.Lock()
        return self._locks[dataset]

    def _checkpoint_path(self, dataset: str) -> str:
        _ensure_dir(INGEST_DIR)
        return os.path.join(INGEST_DIR, f"{dataset}.json")

    def _atomic_write(self, path: str, data: dict) -> None:
        _ensure_dir(os.path.dirname(path))
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, path)

    # ── Load / Create ─────────────────────────────────────────────────

    def get_or_create(self, dataset: str) -> GlobalIngestCheckpoint:
        """Load existing checkpoint or create a new one."""
        path = self._checkpoint_path(dataset)
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
            return self._from_dict(data)

        now = _now_iso()
        cp = GlobalIngestCheckpoint(
            dataset=dataset,
            created_at=now,
            updated_at=now,
        )
        self._save_sync(cp)
        return cp

    async def save(self, checkpoint: GlobalIngestCheckpoint) -> None:
        lock = self._get_lock(checkpoint.dataset)
        async with lock:
            checkpoint.updated_at = _now_iso()
            self._atomic_write(
                self._checkpoint_path(checkpoint.dataset),
                self._to_dict(checkpoint),
            )

    def _save_sync(self, checkpoint: GlobalIngestCheckpoint) -> None:
        checkpoint.updated_at = _now_iso()
        self._atomic_write(
            self._checkpoint_path(checkpoint.dataset),
            self._to_dict(checkpoint),
        )

    # ── Provider state ────────────────────────────────────────────────

    def get_provider_state(
        self, checkpoint: GlobalIngestCheckpoint, provider_name: str
    ) -> ProviderIngestState:
        """Get or create provider state within a checkpoint."""
        if provider_name not in checkpoint.providers:
            checkpoint.providers[provider_name] = ProviderIngestState(
                last_updated=_now_iso(),
            )
        return checkpoint.providers[provider_name]

    def set_container_tag(
        self, checkpoint: GlobalIngestCheckpoint, provider_name: str, container_tag: str
    ) -> None:
        state = self.get_provider_state(checkpoint, provider_name)
        state.container_tag = container_tag

    # ── Turn tracking ─────────────────────────────────────────────────

    def generate_turn_ids(self, session_id: str, message_count: int) -> List[str]:
        """Generate turn IDs for a session's messages."""
        return [f"{session_id}_t{i}" for i in range(message_count)]

    def mark_turns_completed(
        self,
        checkpoint: GlobalIngestCheckpoint,
        provider_name: str,
        turn_ids: List[str],
    ) -> None:
        state = self.get_provider_state(checkpoint, provider_name)
        for tid in turn_ids:
            if tid not in state.completed_turn_ids:
                state.completed_turn_ids.append(tid)
                state.completed_turn_count += 1
        state.last_updated = _now_iso()

    def is_session_ingested(
        self,
        checkpoint: GlobalIngestCheckpoint,
        provider_name: str,
        session_id: str,
        expected_turn_count: int,
    ) -> bool:
        """Check if all turns for a session have been ingested."""
        state = self.get_provider_state(checkpoint, provider_name)
        expected_ids = set(self.generate_turn_ids(session_id, expected_turn_count))
        return expected_ids.issubset(set(state.completed_turn_ids))

    # ── Question coverage ─────────────────────────────────────────────

    def mark_question_covered(
        self, checkpoint: GlobalIngestCheckpoint, provider_name: str, question_id: str
    ) -> None:
        state = self.get_provider_state(checkpoint, provider_name)
        if question_id not in state.questions_covered:
            state.questions_covered.append(question_id)
        state.last_updated = _now_iso()

    def is_question_covered(
        self, checkpoint: GlobalIngestCheckpoint, provider_name: str, question_id: str
    ) -> bool:
        state = self.get_provider_state(checkpoint, provider_name)
        return question_id in state.questions_covered

    # ── Session ingestion records (for async tracking) ────────────────

    def upsert_session_ingestion(
        self,
        checkpoint: GlobalIngestCheckpoint,
        provider_name: str,
        record: SessionIngestionRecord,
    ) -> None:
        state = self.get_provider_state(checkpoint, provider_name)
        if state.session_ingestions is None:
            state.session_ingestions = []
        # Update existing or append
        for i, existing in enumerate(state.session_ingestions):
            if existing.session_id == record.session_id:
                state.session_ingestions[i] = record
                return
        state.session_ingestions.append(record)

    def get_in_flight_sessions(
        self, checkpoint: GlobalIngestCheckpoint, provider_name: str
    ) -> List[SessionIngestionRecord]:
        """Get sessions that are still being ingested (queued/processing)."""
        state = self.get_provider_state(checkpoint, provider_name)
        if not state.session_ingestions:
            return []
        return [
            s for s in state.session_ingestions
            if s.status in ("queued", "processing", "polling_retry")
        ]

    # ── Serialization ─────────────────────────────────────────────────

    @staticmethod
    def _to_dict(cp: GlobalIngestCheckpoint) -> dict:
        data = {
            "dataset": cp.dataset,
            "created_at": cp.created_at,
            "updated_at": cp.updated_at,
            "total_sessions_ingested": cp.total_sessions_ingested,
            "total_turns_ingested": cp.total_turns_ingested,
            "providers": {},
        }
        for pname, pstate in cp.providers.items():
            pdata = {
                "completed_turn_count": pstate.completed_turn_count,
                "completed_turn_ids": pstate.completed_turn_ids,
                "questions_covered": pstate.questions_covered,
                "container_tag": pstate.container_tag,
                "last_updated": pstate.last_updated,
            }
            if pstate.session_ingestions:
                pdata["session_ingestions"] = [asdict(s) for s in pstate.session_ingestions]
            data["providers"][pname] = pdata
        return data

    @staticmethod
    def _from_dict(data: dict) -> GlobalIngestCheckpoint:
        cp = GlobalIngestCheckpoint(
            dataset=data.get("dataset", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            total_sessions_ingested=data.get("total_sessions_ingested", 0),
            total_turns_ingested=data.get("total_turns_ingested", 0),
        )
        for pname, pdata in data.get("providers", {}).items():
            session_ingestions = None
            if pdata.get("session_ingestions"):
                session_ingestions = [
                    SessionIngestionRecord(**s) for s in pdata["session_ingestions"]
                ]
            cp.providers[pname] = ProviderIngestState(
                completed_turn_count=pdata.get("completed_turn_count", 0),
                completed_turn_ids=pdata.get("completed_turn_ids", []),
                questions_covered=pdata.get("questions_covered", []),
                container_tag=pdata.get("container_tag", ""),
                last_updated=pdata.get("last_updated", ""),
                session_ingestions=session_ingestions,
            )
        return cp
