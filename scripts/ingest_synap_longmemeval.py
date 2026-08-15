"""
Synap-only ingestion for LongMemEval dataset.

Ingests all deduplicated turns into Synap memory. Each session is processed
sequentially (turns within a session must be ordered). Sessions are processed
concurrently up to --concurrency (default 4) to speed things up.

Progress is saved to a checkpoint file so the script can be safely interrupted
and resumed with the same command.

Usage:
    uv run python scripts/ingest_synap_longmemeval.py
    uv run python scripts/ingest_synap_longmemeval.py --concurrency 4
    uv run python scripts/ingest_synap_longmemeval.py --reset   # restart from scratch
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── Environment ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# ── Checkpoint file ───────────────────────────────────────────────────────────
CHECKPOINT_PATH = ROOT / "results" / "synap_longmemeval_ingest_checkpoint.json"


def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH) as f:
            return json.load(f)
    return {"completed_sessions": [], "failed_sessions": {}, "started_at": None, "stats": {}}


def save_checkpoint(cp: dict):
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(cp, f, indent=2)


# ── Progress tracking ─────────────────────────────────────────────────────────
class Progress:
    def __init__(self, total_sessions: int, total_turns: int):
        self.total_sessions = total_sessions
        self.total_turns = total_turns
        self.done_sessions = 0
        self.done_turns = 0
        self.failed_sessions = 0
        self.start_time = time.monotonic()
        self._lock = asyncio.Lock()

    async def session_done(self, session_id: str, turns: int, elapsed_s: float, ok: bool):
        async with self._lock:
            if ok:
                self.done_sessions += 1
                self.done_turns += turns
            else:
                self.failed_sessions += 1
            elapsed_total = time.monotonic() - self.start_time
            rate = self.done_turns / elapsed_total if elapsed_total > 0 else 0
            remaining_turns = self.total_turns - self.done_turns
            eta_s = remaining_turns / rate if rate > 0 else 0
            eta_str = f"{eta_s/60:.0f}m" if eta_s > 60 else f"{eta_s:.0f}s"
            status = "✓" if ok else "✗"
            print(
                f"  {status} [{self.done_sessions + self.failed_sessions}/{self.total_sessions}] "
                f"session={session_id[:30]:<30} turns={turns:>3} "
                f"time={elapsed_s:.1f}s  "
                f"total={self.done_turns}/{self.total_turns} turns  "
                f"ETA={eta_str}",
                flush=True,
            )


# ── Synap adapter setup ───────────────────────────────────────────────────────
def build_synap_adapter():
    import yaml
    from adapters.synap_adapter import SynapAdapter

    config_path = ROOT / "configs" / "synap.yaml"
    with open(config_path) as f:
        raw = f.read()

    # Resolve ${ENV_VAR} substitutions
    import re
    def replace_env(m):
        key = m.group(1)
        val = os.environ.get(key, "")
        return val
    raw = re.sub(r'\$\{([^}]+)\}', replace_env, raw)
    config = yaml.safe_load(raw)

    adapter = SynapAdapter(config)
    adapter.initialize()
    print("Synap adapter initialized — credentials loaded from ~/.synap")
    return adapter


# ── Session ingestion ─────────────────────────────────────────────────────────
async def ingest_session(
    adapter,
    session_id: str,
    turns: list,
    semaphore: asyncio.Semaphore,
    progress: Progress,
    checkpoint: dict,
) -> bool:
    """Ingest all turns for one session into Synap. Runs adapter.write() in a
    thread pool since the SynapAdapter uses a synchronous blocking bridge."""

    async with semaphore:
        t0 = time.monotonic()
        loop = asyncio.get_event_loop()
        ok = True

        for turn in turns:
            try:
                await loop.run_in_executor(
                    None,
                    lambda t=turn: adapter.write(
                        session_id,
                        t["turn_id"],
                        t["text"],
                        metadata={**t.get("metadata", {}), "role": t.get("speaker", "user")},
                    ),
                )
            except Exception as e:
                print(f"\n  [ERROR] session={session_id} turn={turn['turn_id']}: {e}", flush=True)
                ok = False
                break

        elapsed = time.monotonic() - t0
        await progress.session_done(session_id, len(turns), elapsed, ok)

        # Update checkpoint
        if ok:
            checkpoint["completed_sessions"].append(session_id)
        else:
            checkpoint["failed_sessions"][session_id] = checkpoint["failed_sessions"].get(session_id, 0) + 1
        save_checkpoint(checkpoint)

        return ok


# ── Main ──────────────────────────────────────────────────────────────────────
async def main(concurrency: int, reset: bool):
    # Load dataset
    print("Loading LongMemEval turns (deduplicated by session)...")
    from datasets.longmemeval.loader import load_all_turns
    all_turns = load_all_turns()
    print(f"  {len(all_turns)} total turns loaded")

    # Group turns by session_id (preserving order)
    sessions: dict = defaultdict(list)
    for turn in all_turns:
        sid = turn["metadata"].get("session_id", "default")
        sessions[sid].append(turn)
    session_list = list(sessions.items())  # [(session_id, [turns...]), ...]
    total_sessions = len(session_list)
    total_turns = len(all_turns)
    print(f"  {total_sessions} unique sessions, {total_turns} turns\n")

    # Load / reset checkpoint
    checkpoint = load_checkpoint() if not reset else {
        "completed_sessions": [], "failed_sessions": {}, "started_at": None, "stats": {}
    }
    if reset and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("Checkpoint reset.\n")

    if not checkpoint["started_at"]:
        checkpoint["started_at"] = datetime.now(timezone.utc).isoformat()

    completed = set(checkpoint["completed_sessions"])
    pending = [(sid, turns) for sid, turns in session_list if sid not in completed]

    if not pending:
        print("All sessions already ingested! Nothing to do.")
        print(f"Checkpoint: {CHECKPOINT_PATH}")
        return

    print(
        f"Progress: {len(completed)}/{total_sessions} sessions already done, "
        f"{len(pending)} remaining\n"
    )

    # Initialize Synap
    print("Initializing Synap SDK...")
    adapter = build_synap_adapter()
    print()

    # Ingest
    print(f"Starting ingestion — concurrency={concurrency}")
    print("─" * 90)

    semaphore = asyncio.Semaphore(concurrency)
    progress = Progress(total_sessions=len(pending), total_turns=sum(len(t) for _, t in pending))

    tasks = [
        ingest_session(adapter, sid, turns, semaphore, progress, checkpoint)
        for sid, turns in pending
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Summary
    n_ok = sum(1 for r in results if r is True)
    n_fail = sum(1 for r in results if r is not True)
    total_elapsed = time.monotonic() - progress.start_time

    print("\n" + "─" * 90)
    print("Ingestion complete:")
    print(f"  Sessions succeeded : {n_ok}")
    print(f"  Sessions failed    : {n_fail}")
    print(f"  Total elapsed      : {total_elapsed/60:.1f} min")
    print(f"  Checkpoint saved   : {CHECKPOINT_PATH}")

    checkpoint["stats"] = {
        "sessions_ok": n_ok,
        "sessions_failed": n_fail,
        "elapsed_seconds": round(total_elapsed, 1),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    save_checkpoint(checkpoint)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest LongMemEval into Synap")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="Max parallel sessions (default: 4)")
    parser.add_argument("--reset", action="store_true",
                        help="Ignore checkpoint and restart from scratch")
    args = parser.parse_args()

    asyncio.run(main(args.concurrency, args.reset))
