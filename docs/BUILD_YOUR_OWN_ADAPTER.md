# Build Your Own Adapter

This guide takes you from an empty file to a memory system running against
LongMemEval and LoCoMo, appearing in the dashboard next to Synap, Mem0, Zep and
Supermemory.

It assumes you have a memory system with some way to write data and some way to
search it. That system can be a hosted API, a local library, a vector database,
or a hundred lines of your own code. The harness does not care.

Expect this to take under an hour. The interface is five methods.

For the precise definition of every type mentioned here, see
[ADAPTER_SPEC.md](ADAPTER_SPEC.md).

## The mental model

The harness runs a benchmark in five phases: ingest, search, answer, evaluate,
report. Your adapter is only involved in the first two.

```
   your adapter                       the harness
   ─────────────                      ───────────
   ingest(sessions)      <────  here are 940 conversations, store them
   await_indexing()      <────  tell me when they are actually searchable
   search(question)      <────  question 1 of 500, what context is relevant?
                          ────> feeds your context to the answer model
                          ────> LLM judge scores the answer
                          ────> writes accuracy, latency, retrieval metrics
```

You are not asked to answer questions. You are asked to remember, and then to
retrieve. The quality of what `search()` returns is what the benchmark actually
measures.

## Step 1: Set up

```bash
git clone https://github.com/maximem-ai/memory_and_context_eval_harness.git
cd memory_and_context_eval_harness
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Put your `OPENAI_API_KEY` in `.env`. That is the answer model and the judge, and
it is required whichever memory system you are testing.

## Step 2: Write the class

Create `adapters/my_adapter.py`. Here is a complete, working adapter for an
imaginary service. Read the comments, they are the parts that matter.

```python
"""
MyMemory provider.

Stores each session as a document and retrieves with semantic search.
"""

import logging
import os
from typing import Any, List

from runner.types import (
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


class MyMemoryProvider(Provider):
    # Must match the key you register, and the config filename.
    name = "my-memory"

    # How many questions the harness may process in parallel. Start low.
    # Raise it once you know your rate limits.
    concurrency = ConcurrencyConfig(default=10)

    def __init__(self):
        # No credentials here. CI instantiates this with an empty
        # environment, so __init__ must never fail.
        self.client = None
        self.top_k = 10

    async def initialize(self, config: ProviderConfig) -> None:
        # Import the SDK here, not at the top of the file. This keeps the
        # test suite runnable without installing every vendor package.
        try:
            from mymemory import MyMemoryClient
        except ImportError:
            raise ImportError(
                "mymemory is required. Install with: pip install mymemory"
            )

        api_key = config.api_key or os.environ.get("MYMEMORY_API_KEY", "")
        if not api_key:
            raise ValueError("MYMEMORY_API_KEY is required (config or env var)")

        self.client = MyMemoryClient(api_key=api_key)

        # Anything in your YAML that is not api_key or base_url lands here.
        self.top_k = config.extras.get("top_k", 10)

        logger.info("MyMemory provider initialised")

    async def ingest(
        self, sessions: List[UnifiedSession], options: IngestOptions
    ) -> IngestResult:
        document_ids: List[str] = []
        session_ingestions: List[SessionIngestion] = []

        for session in sessions:
            # Keep the timestamps. Benchmarks have a whole temporal
            # reasoning category and dropping them costs you every point
            # in it.
            text = "\n".join(
                f"[{m.timestamp or 'unknown'}] {m.role}: {m.content}"
                for m in session.messages
            )

            try:
                # container_tag is the isolation key. Map it to whatever
                # your system uses for tenancy: user, namespace,
                # collection. Skip this and runs will read each other's
                # data.
                doc_id = self.client.write(
                    namespace=options.container_tag,
                    text=text,
                )
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
                # Record the failure and keep going. One bad session
                # should not kill a run that takes hours.
                logger.error("ingest failed for %s: %s", session.session_id, e)
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
        # Do not skip this. If you return before your index is ready, the
        # search phase queries an empty store and your system scores far
        # below what it deserves.
        #
        # If your service exposes an indexing status, poll it. Only fall
        # back to a fixed sleep if it does not.
        import asyncio

        for _ in range(60):
            if self.client.index_ready(namespace=container_tag):
                return
            await asyncio.sleep(1.0)

        logger.warning("indexing did not settle within 60s for %s", container_tag)

    async def search(self, query: str, options: SearchOptions) -> List[Any]:
        limit = options.limit or self.top_k
        try:
            hits = self.client.search(
                namespace=options.container_tag,
                query=query,
                limit=limit,
            )
        except Exception as e:
            # Return empty, never raise. One failed query costs one
            # question. A raised exception costs the whole run.
            logger.error("search failed: %s", e)
            return []

        # Any dict shape is fine. Include "score" if you have one, the
        # retrieval metrics will use it.
        return [
            {"id": h.id, "memory": h.text, "score": h.score}
            for h in hits
        ][:limit]

    async def clear(self, container_tag: str) -> None:
        try:
            self.client.delete_namespace(namespace=container_tag)
        except Exception as e:
            logger.warning("clear failed for %s: %s", container_tag, e)
```

## Step 3: Register it

In `adapters/__init__.py`:

```python
PROVIDER_REGISTRY = {
    "synap": "adapters.synap_adapter.SynapProvider",
    "mem0": "adapters.mem0_adapter.Mem0Provider",
    "zep": "adapters.zep_adapter.ZepProvider",
    "supermemory": "adapters.supermemory_adapter.SupermemoryProvider",
    "my-memory": "adapters.my_adapter.MyMemoryProvider",   # add this
}
```

An unregistered provider cannot be run, however correct its code.

## Step 4: Configure it

Create `configs/my-memory.yaml`. The filename must match `name`.

```yaml
api_key: "${MYMEMORY_API_KEY}"
top_k: 10
```

Add `MYMEMORY_API_KEY=...` to your `.env`. A value written as `${VAR}` is
replaced with that environment variable when the run starts.

## Step 5: Check the contract

```bash
pytest tests/unit/test_provider_contract.py -v
```

This is the same check CI runs. It confirms your class imports, subclasses
`Provider`, implements all five methods as coroutines, declares a `name` that
matches its registry key, and can be constructed without credentials. It does
not call your service, so it is fast and needs no API keys.

Fix anything red here before going further. Every failure at this stage would
otherwise show up an hour into a benchmark run.

## Step 6: Run it

```bash
python -m runner.server
```

Open the dashboard at `http://localhost:8766`, pick your provider and a
benchmark, and start with a small question count. Ten questions is enough to
tell you whether ingest and search are wired correctly.

Run artifacts land in `data/runs/<run-id>/`:

| File | Contents |
|---|---|
| `checkpoint.json` | Per-question state. A run resumes from this if interrupted. |
| `report.json` | Accuracy, per-category breakdown, latency, retrieval metrics. |
| `results/` | Raw retrieval output per question. |

## Step 7: Read your own failures

The single most useful debugging step is to open `checkpoint.json`, find a
question your system got wrong, and look at what `search()` actually returned
for it.

Nearly every low score falls into one of four buckets:

**Nothing was retrieved.** Your `await_indexing()` returned too early, or your
`container_tag` mapping is wrong so you are searching an empty namespace.

**The right fact was retrieved but ranked low.** Check whether you are
respecting `options.limit`, and whether the harness is truncating your results
before the answer model sees them.

**The right fact was never stored.** Look at what you built in `ingest()`. A
common cause is dropping message timestamps or the speaker role, which makes
temporal and multi-session questions unanswerable.

**The fact was stored and retrieved, but stale.** Knowledge-update questions
deliberately contradict earlier statements. Systems that append without
superseding tend to return both the old and the new fact, and the answer model
picks the wrong one.

## Submitting your adapter

Pull requests adding a provider are welcome. Please include:

- The adapter and its `configs/<name>.yaml`, with secrets as `${ENV_VAR}`.
- The registry entry.
- A green `pytest tests`.
- A note in the PR describing what you ran and what you scored, including the
  answer model, the judge, and the question count. Numbers without that context
  are not comparable, which is the whole reason this harness exists.

Please do not commit API keys, `.env`, or run artifacts. `data/` and `results/`
are already in `.gitignore`.
