# Provider Specification

This is the reference for the `Provider` interface. If you want a walkthrough
that builds a working adapter step by step, read
[BUILD_YOUR_OWN_ADAPTER.md](BUILD_YOUR_OWN_ADAPTER.md) first and come back here
for the details.

Everything below is defined in [`runner/types.py`](../runner/types.py). That
file is the source of truth. If this document and that file ever disagree, the
file wins and this document is a bug.

## The contract

A provider is a class that inherits `runner.types.Provider` and implements five
async methods. The harness never talks to your memory system directly, only
through these.

```python
from runner.types import Provider

class MyProvider(Provider):
    name = "my-provider"

    async def initialize(self, config: ProviderConfig) -> None: ...
    async def ingest(self, sessions: list[UnifiedSession],
                     options: IngestOptions) -> IngestResult: ...
    async def await_indexing(self, result: IngestResult, container_tag: str,
                             on_progress=None) -> None: ...
    async def search(self, query: str, options: SearchOptions) -> list: ...
    async def clear(self, container_tag: str) -> None: ...
```

All five are abstract. Python will refuse to instantiate a subclass that misses
any of them, and the contract tests in
[`tests/unit/test_provider_contract.py`](../tests/unit/test_provider_contract.py)
check this on every push.

## Class attributes

| Attribute | Required | Purpose |
|---|---|---|
| `name` | yes | Must equal the key you register in `PROVIDER_REGISTRY`. Run artifacts are labelled with it. |
| `concurrency` | no | A `ConcurrencyConfig` giving your default parallelism. Providers with generous rate limits set this high. |
| `prompts` | no | A `ProviderPrompts` if your system needs a custom answer or judge prompt. Most providers leave this unset. |

## Lifecycle

The harness drives one benchmark run in this order.

1. **`initialize(config)`** is called once. Create your client here, not in
   `__init__`. Construction must succeed without credentials, because the
   contract tests instantiate every registered provider with no environment
   set.

2. **`ingest(sessions, options)`** is called with a batch of conversation
   sessions to load into memory. Return an `IngestResult` describing what you
   queued.

3. **`await_indexing(result, container_tag, on_progress)`** blocks until that
   data is actually searchable. This exists because most memory systems index
   asynchronously. Getting this wrong is the single most common cause of a
   falsely low score: if you return before indexing finishes, the search phase
   queries an empty store and your system looks worse than it is.

4. **`search(query, options)`** is called once per benchmark question. Return
   the context your system thinks is relevant. The harness feeds it to the
   answer model.

5. **`clear(container_tag)`** removes everything written under that tag, so
   runs do not contaminate each other.

## `container_tag`, the isolation key

Every ingest and every search carries a `container_tag`. It is the harness's
way of saying "this data belongs to this run and this question set, keep it
separate from everything else".

Map it onto whatever your system uses for tenancy: a user id, a namespace, a
collection, a partition key. Zep maps it to `user_id`. Synap maps it to a
container. If your system has no isolation concept, prefix your keys with it.

Two runs using the same tag will read each other's data and your numbers will
be wrong in a way that is very hard to notice.

## Types

### Input

```python
@dataclass
class UnifiedMessage:
    role: Literal["user", "assistant"]
    content: str
    timestamp: Optional[str] = None
    speaker: Optional[str] = None

@dataclass
class UnifiedSession:
    session_id: str
    messages: List[UnifiedMessage]
    metadata: Optional[Dict[str, Any]] = None
```

`timestamp` matters. Benchmarks like LongMemEval have a whole temporal
reasoning category, and a system that discards message timestamps will score
near zero on it.

### Options

```python
@dataclass
class ProviderConfig:
    api_key: str = ""
    base_url: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)

@dataclass
class IngestOptions:
    container_tag: str
    metadata: Optional[Dict[str, Any]] = None

@dataclass
class SearchOptions:
    container_tag: str
    limit: Optional[int] = None
    threshold: Optional[float] = None
    on_request_log: Optional[Callable[[ProviderRequestLog], None]] = None
```

Anything in your YAML config that is not `api_key` or `base_url` arrives in
`config.extras`.

`on_request_log` is optional. Call it with a `ProviderRequestLog` if you want
raw request and response payloads captured in the run artifacts. It is useful
when debugging why a score moved.

### Output

```python
@dataclass
class IngestResult:
    document_ids: List[str] = field(default_factory=list)
    task_ids: Optional[List[str]] = None
    session_ingestions: Optional[List[SessionIngestion]] = None

@dataclass
class SessionIngestion:
    session_id: str
    ingestion_id: Optional[str] = None
    status: SessionIngestionStatus = "queued"   # queued | processing | completed
                                                # | failed | unknown_completed
                                                # | polling_retry
    turn_count: Optional[int] = None
    memories_created: Optional[int] = None
    error: Optional[str] = None
    # also: queued_at, completed_at, batch_id

@dataclass
class IndexingProgress:
    completed_ids: List[str]
    failed_ids: List[str]
    total: int
```

Populate `session_ingestions` even when everything succeeds. The dashboard and
the checkpoint files use it to show per-session progress and to resume an
interrupted run.

`search()` returns a plain `list`. There is no required element shape, because
different memory systems return genuinely different things: flat facts, graph
edges, document chunks. Return dictionaries with whatever your system produced.
Include a `score` key if you have a relevance score, since the retrieval
metrics use it when present.

## Optional async ingestion

If your system accepts writes and indexes them in the background, you can
implement two extra methods and let the harness poll instead of blocking:

```python
async def ingest_fire_and_forget(self, sessions, options) -> IngestResult: ...
async def check_ingestion_status(self, ingestion_id: str) -> IngestionStatus: ...
```

Both raise `NotImplementedError` by default. Skip them unless you need them.

## Registration

Add your class to `PROVIDER_REGISTRY` in
[`adapters/__init__.py`](../adapters/__init__.py):

```python
PROVIDER_REGISTRY = {
    "synap": "adapters.synap_adapter.SynapProvider",
    "my-provider": "adapters.my_adapter.MyProvider",
}
```

The value is a dotted path, imported lazily. A provider that is not in this
registry cannot be run, no matter how correct its code is.

## Configuration

Create `configs/<name>.yaml`, matching your `name` exactly. A value written as
`${SOME_VAR}` is replaced with that environment variable at load time, or an
empty string if it is unset.

```yaml
api_key: "${MY_PROVIDER_API_KEY}"
base_url: "https://api.example.com"
top_k: 10
```

`api_key` and `base_url` populate the matching `ProviderConfig` fields. Every
other key lands in `config.extras`.

## Rules that are easy to miss

- **Import your vendor SDK inside `initialize()`, not at module top level.**
  Every adapter in this repo does. It keeps the test suite runnable without
  installing every vendor's package, and it means a broken dependency in one
  provider cannot stop the others from loading.
- **Never let `__init__` require credentials.** It runs in CI with an empty
  environment.
- **Do not raise out of `search()`.** Log the error and return an empty list.
  One failed query should cost you one question, not the whole run.
- **Respect `options.limit`.** Returning more context than asked for inflates
  scores and makes cross-provider comparison meaningless.
