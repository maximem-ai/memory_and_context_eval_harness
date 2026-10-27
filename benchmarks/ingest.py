"""Resumable ingest for LongMemEval-S.

Ingest is by far the long pole: sessions within one user are indexed serially
by the service, so a run that cannot resume would mean re-paying hours for any
interruption. The checkpoint records, per Synap user, which session documents
were submitted and which were confirmed stored, and a resumed run skips
exactly those.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

from runner.types import IngestOptions, IngestResult, SearchOptions, UnifiedSession

logger = logging.getLogger("benchmarks.ingest")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class IngestCheckpoint:
    """Durable, crash-safe record of which sessions are actually in the store.

    Three properties this needs:

    * **Crash safety.** The JSON snapshot is rewritten atomically, but a
      process killed mid-user would lose that user's progress. So every
      confirmed session is ALSO appended to a sidecar `.events.jsonl` the
      moment it is confirmed, and `load()` replays the sidecar over the
      snapshot. Appends survive a kill; a half-written rewrite does not.
    * **Identity.** A checkpoint carries a fingerprint of the dataset it was
      built against. Resuming an ingest against a checkpoint from a different
      split would silently skip the wrong sessions, so a mismatch refuses to
      resume rather than guessing.
    * **Truth.** Only sessions a retrieval or an ingestion job's own outcome
      confirmed are recorded as done. Job status alone is not evidence: a
      wedged job can report `queued` indefinitely and never run.
    """

    def __init__(self, path: str, fingerprint: Optional[str] = None,
                 benchmark: str = "", run_id: str = ""):
        self.path = path
        self.events_path = path.replace(".json", "") + ".events.jsonl"
        self.data: Dict[str, Dict[str, str]] = {}
        self.meta: Dict[str, Any] = {
            "benchmark": benchmark, "run_id": run_id,
            "dataset_fingerprint": fingerprint,
            "created_at": _now(), "updated_at": _now(),
        }
        self._load(fingerprint)

    def _load(self, fingerprint: Optional[str]) -> None:
        snap: Dict[str, Any] = {}
        if os.path.exists(self.path):
            try:
                with open(self.path) as fh:
                    snap = json.load(fh)
            except Exception as e:
                logger.warning("checkpoint snapshot unreadable (%s); "
                               "rebuilding from the event log", e)
        if isinstance(snap, dict) and "sessions" in snap:
            self.data = snap.get("sessions") or {}
            old_meta = snap.get("meta") or {}
        else:
            self.data = {}
            old_meta = {}

        prior = old_meta.get("dataset_fingerprint")
        if prior and fingerprint and prior != fingerprint:
            raise RuntimeError(
                f"checkpoint {self.path} was built against dataset "
                f"{prior[:12]} but this run is {fingerprint[:12]}. Resuming "
                f"would skip the wrong sessions. Move it aside or pass a new "
                f"--checkpoint-tag.")
        if old_meta:
            self.meta.update({k: v for k, v in old_meta.items()
                              if k in ("created_at", "run_id")})

        # Replay the append-only log over the snapshot.
        replayed = 0
        if os.path.exists(self.events_path):
            with open(self.events_path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # torn final line after a kill
                    u, sid, st = ev.get("u"), ev.get("s"), ev.get("st")
                    if u and sid:
                        self.data.setdefault(u, {})[sid] = st or "completed"
                        replayed += 1
        if self.data:
            logger.info("ingest checkpoint: resuming %d users, %d sessions "
                        "(%d replayed from the event log)",
                        len(self.data), self.totals["sessions_done"], replayed)

    # ── reads ──────────────────────────────────────────────────────────
    def done_sessions(self, user_id: str) -> set:
        return {sid for sid, st in self.data.get(user_id, {}).items()
                if st in ("completed", "unknown_completed")}

    def posted_sessions(self, user_id: str) -> set:
        """Sessions this run has already POSTed, whatever became of them.

        A POST is not idempotent: the server has no request key, so re-sending
        a document stores it a second time and the duplicate is invisible to
        every later retrieval. `done_sessions` cannot carry that, because it
        only holds sessions confirmed present, and confirmation arrives long
        after the POST. So the submission itself is recorded the moment
        batch_create returns, under the state `submitted`, which is
        deliberately NOT in done_sessions: the session still has to be
        confirmed before it counts as ingested. It just must never be sent
        twice.
        """
        return set(self.data.get(user_id, {}))

    def user_complete(self, user_id: str, expected: int) -> bool:
        return len(self.done_sessions(user_id)) >= expected

    @property
    def totals(self) -> Dict[str, int]:
        return {"users": len(self.data),
                "sessions_done": sum(len(self.done_sessions(u)) for u in self.data)}

    # ── writes ─────────────────────────────────────────────────────────
    def record(self, user_id: str, states: Dict[str, str]) -> None:
        """Record states and append them to the crash-safe log immediately."""
        if not states:
            return
        self.data.setdefault(user_id, {}).update(states)
        os.makedirs(os.path.dirname(self.events_path) or ".", exist_ok=True)
        with open(self.events_path, "a") as fh:
            for sid, st in states.items():
                fh.write(json.dumps({"u": user_id, "s": sid, "st": st,
                                     "t": _now()}) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def flush(self) -> None:
        self.meta["updated_at"] = _now()
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"meta": self.meta, "sessions": self.data}, fh, indent=0)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


class UserRotation:
    """natural user id -> the user id its memories actually live under.

    A Synap user's ingest queue can wedge permanently: every document for that
    user stays `queued` and is never processed, while other users on the same
    instance are fine, and there is no API to unwedge it. Re-sending into the
    same user is therefore useless; the only recovery is a fresh user id.

    Isolation is unchanged: still exactly one user per question. The abandoned
    scope holds a partial copy of that same question's own data, so even a
    leak from it could not import another question's content.
    """

    def __init__(self, path: str):
        self.path = path
        self.map: Dict[str, str] = {}
        if os.path.exists(path):
            try:
                with open(path) as fh:
                    self.map = json.load(fh)
            except Exception:
                self.map = {}

    def effective(self, natural: str) -> str:
        return self.map.get(natural, natural)

    def rotate(self, natural: str) -> str:
        cur = self.effective(natural)
        n = 2
        tail = cur.rsplit("_r", 1)[-1]
        if tail.isdigit():
            n = int(tail) + 1
        new = f"{natural}_r{n}"
        self.map[natural] = new
        self.flush()
        return new

    def flush(self) -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.map, fh)
        os.replace(tmp, self.path)


def dump_ingested_documents(provider, user_id: str, sessions: List[Any],
                            out_dir: str) -> str:
    """Write the EXACT document text sent to Synap, per session.

    Without this the trace is incomplete: you can see what was retrieved and
    what the model answered, but not what the memory system was actually given
    to work with. Opt-in (--dump-ingested), because the S haystacks are large.
    """
    os.makedirs(out_dir, exist_ok=True)
    inline_ts = bool(provider._config_extras.get("inline_turn_timestamps", False))
    payload = {
        "user_id": user_id,
        "inline_turn_timestamps": inline_ts,
        "documents": [
            {
                "session_id": sess.session_id,
                "document_id": f"longterm:{sess.session_id}",
                "document_created_at": (sess.metadata or {}).get("document_created_at"),
                "turns": len(sess.messages),
                "metadata": sess.metadata,
                # the literal string the adapter builds and sends
                "document": provider._format_session(sess, inline_timestamps=inline_ts),
            }
            for sess in sessions
        ],
    }
    path = os.path.join(out_dir, f"{user_id}.json")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
    return path


async def verify_sessions(provider, user_id: str, sessions: List[Any],
                          ingestions: Optional[List[Any]] = None,
                          already_present: Optional[set] = None,
                          probe_unknown: Optional[set] = None,
                          concurrency: int = 6) -> Dict[str, Any]:
    """Decide, per session, whether it is really in the store.

    Uses the ingestion job's own per-session outcome as the primary evidence,
    not a semantic probe. Probing each session with its first turns and
    counting zero hits as missing produces false negatives: a session can be
    ingested yet extract only an incidental fact from its middle, so the
    opening-turns probe returns nothing. Acting on that would rotate the user
    to a fresh scope and re-ingest for no reason.

    Retrieval carries no session provenance, so per-session presence cannot be
    confirmed from the retrieval side at all. The ingestion status can: it
    reports completion and memories_created for each session individually.

    So per session:
      completed + memories_created > 0  -> present
      completed + memories_created == 0 -> present but empty (the service can
          legitimately extract nothing, for instance from assistant-authored
          content), recorded as such and NOT retried
      never terminal                    -> missing (a wedged queue)

    One user-level retrieval still runs as a sanity check, to catch the case
    where jobs report memories but nothing is queryable.
    """
    by_session: Dict[str, Any] = {}
    for si in (ingestions or []):
        by_session[si.session_id] = si

    # Sessions confirmed by an EARLIER run are skipped this time round, so
    # they carry no ingestion record here. Without this they would be read as
    # missing and a partially-resumed user would be rotated forever.
    known = set(already_present or ())

    present, empty, missing = [], [], []
    probe_failed: List[str] = []
    unconfirmed: List[Any] = []
    # Sessions a KILLED run POSTed: the job ids died with the process, so there
    # is no ingestion record and no status to read, but the document may well
    # be in the store. Calling them missing would be wrong in both directions:
    # it understates what is there, and it is what sends an already-stored
    # document a second time. They go to the retrieval probe instead, which is
    # the only evidence left.
    to_probe = set(probe_unknown or ())

    for sess in sessions:
        si = by_session.get(sess.session_id)
        if si is None:
            if sess.session_id in known:
                present.append(sess.session_id)
            elif sess.session_id in to_probe:
                unconfirmed.append(sess)
            else:
                missing.append(sess.session_id)
            continue
        created = getattr(si, "memories_created", 0) or 0
        if si.status in ("completed", "partial_success", "unknown_completed"):
            (present if created > 0 else empty).append(sess.session_id)
        else:
            # NOT missing yet. A non-terminal status means polling never saw
            # the job finish, which is not the same as the document not being
            # stored: a status outage can leave a fully ingested user looking
            # incomplete. Rotating those would discard good data and
            # re-ingest it into a new scope.
            unconfirmed.append(sess)

    # Last resort before declaring data lost: ask retrieval.
    if unconfirmed:
        csem = asyncio.Semaphore(concurrency)

        async def _confirm(sess):
            async with csem:
                probe = " ".join((m.content or "") for m in sess.messages[:3])[:180]
                try:
                    items = await provider.search(
                        probe, SearchOptions(container_tag=user_id, limit=10,
                                             threshold=None))
                    return sess.session_id, len(items)
                except Exception as e:
                    logger.warning("confirm probe failed for %s/%s: %s",
                                   user_id, sess.session_id, e)
                    return sess.session_id, -1

        for sid, n in await asyncio.gather(*(_confirm(x) for x in unconfirmed)):
            if n > 0:
                present.append(sid)
                logger.info("session %s never reported terminal but IS "
                            "retrievable; counting it present", sid)
            elif n < 0:
                # The probe itself failed. That says nothing about whether the
                # document is stored, so it must not be read as loss: calling
                # it missing rotates the user to a fresh scope and re-ingests
                # data that was already there.
                probe_failed.append(sid)
                logger.warning("session %s could not be confirmed (retrieval "
                               "probe failed); NOT treating as missing", sid)
            else:
                missing.append(sid)

    # Sanity check: if anything was extracted, it must be queryable.
    queryable = None
    if present:
        probe = " ".join((m.content or "") for m in sessions[0].messages[:3])[:180]
        try:
            items = await provider.search(
                probe, SearchOptions(container_tag=user_id, limit=20, threshold=None))
            queryable = len(items)
            if queryable == 0:
                # Second chance with different text before believing it.
                alt = " ".join((m.content or "") for m in sessions[-1].messages[-3:])[:180]
                items = await provider.search(
                    alt, SearchOptions(container_tag=user_id, limit=20, threshold=None))
                queryable = len(items)
        except Exception as e:
            logger.warning("verify probe failed for %s: %s", user_id, e)
            queryable = -1

    return {
        "user_id": user_id,
        "expected": [s.session_id for s in sessions],
        "present": present,
        "extracted_empty": empty,
        "missing": missing,
        "probe_failed": probe_failed,
        # A user is verified when no session is missing. Sessions that
        # genuinely extracted nothing are not a failure to retry.
        # Sessions whose confirmation probe failed are unknown, not lost, so
        # they block "verified" without being queued for re-ingest.
        "verified": not missing and not probe_failed,
        "queryable_items": queryable,
    }


def _record_verification(checkpoint: "IngestCheckpoint", user_id: str,
                         batch_sessions: List[Any], batch_ings: List[Any],
                         part: Dict[str, Any]) -> None:
    """Checkpoint one batch's outcome, with the whole-user rules applied to it.

    A batch that verified is recorded in full; a batch that extracted nothing
    from every session is recorded too, because retrying cannot change what
    the service stored; otherwise only the sessions actually confirmed present
    are recorded, and the rest stay `submitted` so the next pass verifies them
    instead of re-sending them.
    """
    present = set(part["present"]) | set(part["extracted_empty"])
    if batch_ings:
        states = {si.session_id: ("completed" if si.session_id in present
                                  else si.status)
                  for si in batch_ings}
    else:
        # No ingestions: a resume confirming sessions an earlier run POSTed.
        states = {sess.session_id: "completed" for sess in batch_sessions
                  if sess.session_id in present}
    verified_states = {sid: st for sid, st in states.items() if sid in present}

    # Report empty extraction whatever branch we take. A batch whose sessions
    # all indexed but produced no memories is "verified" in the sense that
    # nothing is missing, so the signal would otherwise be lost.
    if part["extracted_empty"]:
        logger.warning(
            "EMPTY EXTRACTION for %s: %d/%d session(s) in this batch indexed "
            "with 0 memories; recorded, not retried",
            user_id, len(part["extracted_empty"]), len(batch_sessions))

    batch_zero = (len(part["extracted_empty"]) == len(batch_sessions)
                  and not part["missing"])
    if part["verified"] or batch_zero:
        checkpoint.record(user_id, states)
    elif verified_states:
        checkpoint.record(user_id, verified_states)


def _merge_verifications(user_id: str, sessions: List[Any],
                         parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fold per-batch verifications into the user-level shape callers expect."""
    merged: Dict[str, Any] = {
        "user_id": user_id,
        "expected": [sess.session_id for sess in sessions],
        "present": [], "extracted_empty": [], "missing": [], "probe_failed": [],
    }
    for part in parts:
        for key in ("present", "extracted_empty", "missing", "probe_failed"):
            merged[key].extend(part.get(key) or [])
    merged["verified"] = not merged["missing"] and not merged["probe_failed"]
    counts = [part.get("queryable_items") for part in parts
              if part.get("queryable_items") is not None]
    # A successful count outranks a -1: -1 means one probe failed, which says
    # nothing about the store, and some other batch did get an answer.
    merged["queryable_items"] = max(counts) if counts else None
    return merged


async def ingest_user(provider, user_id: str, sessions: List[UnifiedSession],
                      checkpoint: IngestCheckpoint, batch_size: int = 5,
                      doc_dump_dir: Optional[str] = None,
                      ) -> Dict[str, Any]:
    """Ingest one user's sessions, skipping any already recorded as terminal."""
    already = checkpoint.done_sessions(user_id)
    # Two skip sets, for two different reasons. `already` is sessions confirmed
    # in the store: nothing more to do with them at all. `posted` is sessions
    # whose POST was accepted but whose confirmation never landed (a kill
    # between the two). They must not be sent again (that is the duplicate),
    # but they still have to be confirmed, so they skip submission and go
    # straight to verification below, where the retrieval probe promotes them.
    posted = checkpoint.posted_sessions(user_id)
    todo = [s for s in sessions if s.session_id not in posted]
    unconfirmed_posted = [s for s in sessions
                          if s.session_id in posted and s.session_id not in already]
    if not todo and not unconfirmed_posted:
        return {"user_id": user_id, "skipped": len(sessions), "submitted": 0,
                "duration_s": 0.0, "resumed": True}
    if unconfirmed_posted:
        logger.warning("%s: %d session(s) were POSTed by an earlier run but "
                       "never confirmed; verifying, NOT re-sending",
                       user_id, len(unconfirmed_posted))

    if doc_dump_dir:
        try:
            dump_ingested_documents(provider, user_id, sessions, doc_dump_dir)
        except Exception as e:
            logger.warning("could not dump ingested docs for %s: %s", user_id, e)

    t0 = time.monotonic()
    submitted = 0
    failed = 0
    errors: List[str] = []
    all_ings = []
    batches: List[Any] = []

    # Submit every batch before waiting: batch_create returns quickly while
    # indexing takes far longer per document, so waiting inside the loop just
    # idles the server on each batch's tail.
    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        try:
            res = await provider.ingest(batch, IngestOptions(container_tag=user_id))
        except Exception as e:
            failed += len(batch)
            errors.append(f"submit: {str(e)[:200]}")
            logger.error("ingest submit failed for %s: %s", user_id, e)
            continue
        accepted = []
        for si in (res.session_ingestions or []):
            if si.status in ("queued", "processing", "completed"):
                submitted += 1
                accepted.append(si.session_id)
            else:
                failed += 1
                if si.error:
                    errors.append(si.error[:200])
        all_ings.extend(res.session_ingestions or [])
        # Ledger the POST before anything else can interrupt. Written and
        # fsynced here, right after batch_create, rather than after indexing
        # completes long afterwards: see posted_sessions().
        if accepted:
            checkpoint.record(user_id, {sid: "submitted" for sid in accepted})
        batches.append((batch, res.session_ingestions or []))

    # Await, verify and CHECKPOINT ONE BATCH AT A TIME.
    #
    # Submission still happens for every batch up front, above, for the reason
    # given there. Confirmation does not wait for the whole user: a scope in
    # the S split is dozens of sessions indexing serially, so a single await
    # over all of them would return only after the last one, and nothing would
    # be checkpointed until then. Any kill inside that window would lose the
    # entire scope and leave a prefix that could not be resumed without
    # duplicating. Per batch, the loss is one batch.
    #
    # Sessions index serially server-side either way, so polling batch 1's
    # subset before batch 2's does not slow indexing down; it only means batch
    # 1 is recorded as soon as it is done instead of at the end.
    def _merge_indexing(acc, part):
        for k, v in (part or {}).items():
            if isinstance(v, (int, float)):
                acc[k] = acc.get(k, 0) + v
            else:
                acc.setdefault(k, v)
        return acc

    summary: Dict[str, Any] = {}
    parts: List[Dict[str, Any]] = []
    for batch_sessions, batch_ings in batches:
        if batch_ings:
            summary = _merge_indexing(summary, await provider.await_indexing(
                IngestResult(session_ingestions=batch_ings), user_id))
        part = await verify_sessions(provider, user_id, batch_sessions,
                                     ingestions=batch_ings,
                                     already_present=already)
        parts.append(part)
        _record_verification(checkpoint, user_id, batch_sessions, batch_ings, part)

    # Sessions an earlier run POSTed but never confirmed: no job ids survive a
    # process death, so there is nothing to await. The retrieval probe is the
    # whole check, and verify_sessions already treats "never reported terminal
    # but IS retrievable" as present.
    if unconfirmed_posted:
        part = await verify_sessions(
            provider, user_id, unconfirmed_posted, ingestions=[],
            already_present=already,
            probe_unknown={sess.session_id for sess in unconfirmed_posted})
        parts.append(part)
        _record_verification(checkpoint, user_id, unconfirmed_posted, [], part)

    verification = _merge_verifications(user_id, sessions, parts)

    # A job that COMPLETED having extracted nothing is not a wedged queue and
    # must not be retried: re-sending the identical document to a new user
    # reproduces it. A wedged queue looks different: it never reaches a
    # terminal state at all.
    extracted = sum(getattr(si, "memories_created", 0) or 0 for si in all_ings)
    zero_extraction = (len(verification["extracted_empty"]) == len(sessions)
                       and not verification["missing"])
    present = set(verification["present"]) | set(verification["extracted_empty"])

    if zero_extraction:
        logger.warning("ZERO EXTRACTION for %s: %d session(s) indexed, 0 memories "
                       "created; not retried", user_id, len(sessions))
    if verification["missing"]:
        logger.error("INCOMPLETE INGEST for %s: %d/%d sessions in the store, "
                     "missing (never indexed) %s; only the confirmed sessions "
                     "were checkpointed",
                     user_id, len(present), len(sessions),
                     verification["missing"][:6])

    return {
        "user_id": user_id,
        "sessions": len(sessions),
        "skipped": len(already),
        "submitted": submitted,
        "failed": failed,
        "errors": errors[:5],
        "indexing": summary,
        "verification": verification,
        "sessions_expected": len(sessions),
        "sessions_present": len(verification["present"]),
        "sessions_extracted_empty": len(verification["extracted_empty"]),
        "sessions_missing": len(verification["missing"]),
        "zero_extraction": zero_extraction,
        "memories_created": extracted,
        "duration_s": round(time.monotonic() - t0, 1),
        "resumed": bool(already),
    }


async def ingest_all(provider, units: List[Any], build_sessions: Callable,
                     checkpoint: IngestCheckpoint, concurrency: int = 1,
                     batch_size: int = 5,
                     on_done: Optional[Callable] = None,
                     rotation: Optional[UserRotation] = None,
                     doc_dump_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Ingest many users. `build_sessions(unit) -> (user_id, [UnifiedSession])`."""
    sem = asyncio.Semaphore(concurrency)
    reports: List[Dict[str, Any]] = []
    lock = asyncio.Lock()
    total = len(units)

    async def one(idx: int, unit: Any) -> None:
        natural, sessions = build_sessions(unit)
        user_id = rotation.effective(natural) if rotation else natural
        if checkpoint.user_complete(user_id, len(sessions)):
            async with lock:
                reports.append({"user_id": user_id, "natural_user_id": natural,
                                "skipped": len(sessions), "submitted": 0,
                                "resumed": True, "duration_s": 0.0})
            return
        async with sem:
            r = await ingest_user(provider, user_id, sessions, checkpoint,
                                  batch_size=batch_size, doc_dump_dir=doc_dump_dir)
            r["natural_user_id"] = natural
        async with lock:
            reports.append(r)
            checkpoint.flush()
            n = len(reports)
            if n % 5 == 0 or n == total:
                t = checkpoint.totals
                logger.info("ingest %d/%d users | %d sessions indexed so far",
                            n, total, t["sessions_done"])
            if on_done:
                on_done(r)

    await asyncio.gather(*(one(i, u) for i, u in enumerate(units)))
    checkpoint.flush()

    # Retry any user whose memories are not retrievable. Whatever the job
    # status said, that user was not ingested, and its questions would
    # otherwise be scored against nothing.
    for attempt in (1, 2):
        bad = [r for r in reports
               if r.get("verification") and not r["verification"]["verified"]
               and not r.get("zero_extraction")]
        if not bad:
            break
        logger.warning("re-ingesting %d unverified user(s), attempt %d",
                       len(bad), attempt)
        by_natural = {}
        for u in units:
            nid, sess = build_sessions(u)
            by_natural[nid] = sess
        for r in bad:
            natural = r.get("natural_user_id") or r["user_id"]
            if rotation is not None:
                # Re-sending into a wedged queue never drains it.
                target = rotation.rotate(natural)
                logger.warning("rotating %s -> %s (wedged ingest queue)",
                               r["user_id"], target)
            else:
                target = r["user_id"]
            new_r = await ingest_user(provider, target, by_natural[natural],
                                      checkpoint, batch_size=batch_size,
                                      doc_dump_dir=doc_dump_dir)
            new_r["natural_user_id"] = natural
            reports[reports.index(r)] = new_r
        checkpoint.flush()

    still = [r["user_id"] for r in reports
             if r.get("verification") and not r["verification"]["verified"]
             and not r.get("zero_extraction")]
    zero = [r["user_id"] for r in reports if r.get("zero_extraction")]
    if zero:
        logger.warning("ZERO EXTRACTION for %d user(s): the ingest completed and the "
                       "system stored no memories. Their questions will be scored "
                       "against an empty memory, which is the real product behaviour.",
                       len(zero))
    if still:
        logger.error("INGEST FAILED after retries for %d user(s): %s",
                     len(still), still[:10])
    return reports
