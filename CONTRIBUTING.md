# Contributing to Memory & Context Eval Harness

Thank you for your interest in contributing. This guide covers everything you need to get started.

## Development Setup

```bash
git clone https://github.com/gauravmaximem/memory_and_context_eval_harness.git
cd memory_and_context_eval_harness
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Frontend
cd frontend && npm install
```

## Project Structure

```
runner/              Core pipeline engine
  orchestrator.py    Main orchestrator (ingest, run, compare)
  server.py          FastAPI + WebSocket server
  types.py           All type definitions (Provider, Benchmark, etc.)
  checkpoint.py      Run checkpoint manager
  phases/            Pipeline phase implementations
    global_ingest.py   Session ingestion (global + isolated)
    search.py          Context retrieval
    answer.py          LLM answer generation
    evaluate.py        Judge scoring + retrieval metrics
    report.py          Metrics aggregation

adapters/            Memory provider integrations
  base_adapter.py    Re-exports Provider ABC
  synap_adapter.py   Synap (maximem-synap SDK)
  mem0_adapter.py    Mem0 (mem0ai SDK)
  zep_adapter.py     Zep (zep-cloud SDK)
  supermemory_adapter.py  Supermemory (supermemory SDK)

datasets/            Benchmark loaders
  base.py            Benchmark factory + registry
  longmemeval/       LongMemEval benchmark

scorers/             Evaluation logic
  llm_judge.py       5 judge prompts + retrieval quality eval

prompts/             System prompts
  qa_agent.md   Default answering prompt
  judge.md           Judge prompt override

frontend/            Next.js 15 dashboard
  app/               Pages (runs, compare, leaderboard, ingest)
  components/        Reusable UI components
  lib/               API client + utilities
```

## Adding a Memory Provider

1. Create `adapters/your_provider.py`:

```python
from adapters.base_adapter import (
    Provider, ProviderConfig, IngestOptions, IngestResult,
    SearchOptions, UnifiedSession, SessionIngestion,
)

class YourProvider(Provider):
    name = "your-provider"

    async def initialize(self, config: ProviderConfig) -> None:
        # Initialize SDK client using config.api_key, config.base_url, config.extras
        pass

    async def ingest(self, sessions: list[UnifiedSession], options: IngestOptions) -> IngestResult:
        # Ingest sessions into your provider
        # options.container_tag is the namespace for data isolation
        # Return IngestResult with document_ids and session_ingestions
        pass

    async def await_indexing(self, result: IngestResult, container_tag: str, on_progress=None) -> None:
        # Wait until ingested data is searchable
        # Call on_progress(IndexingProgress(...)) periodically
        pass

    async def search(self, query: str, options: SearchOptions) -> list:
        # Retrieve relevant context for a query
        # options.container_tag scopes the search
        # options.limit is max results (default 10)
        # Return list of result objects (provider-specific format is fine)
        pass

    async def clear(self, container_tag: str) -> None:
        # Delete all data for a container
        pass
```

2. Create `configs/your_provider.yaml`:

```yaml
api_key: "${YOUR_PROVIDER_API_KEY}"
top_k: 10
```

3. Register in `adapters/__init__.py`:

```python
PROVIDER_REGISTRY["your-provider"] = "adapters.your_provider.YourProvider"
```

4. Add API key to `.env.example`:

```
YOUR_PROVIDER_API_KEY=
```

### Provider Interface Details

| Method | When Called | What It Should Do |
|--------|------------|-------------------|
| `initialize(config)` | Once at startup | Connect to your API, validate credentials |
| `ingest(sessions, options)` | During ingestion phase | Store conversation sessions, return tracking IDs |
| `await_indexing(result, tag)` | After ingestion | Poll until data is searchable |
| `search(query, options)` | During search phase | Return relevant context for a question |
| `clear(tag)` | On reset/cleanup | Delete all data for a container |

### Optional: Async Ingestion

If your provider processes data asynchronously, override these:

```python
async def ingest_fire_and_forget(self, sessions, options) -> IngestResult:
    # Submit without waiting, return ingestion IDs for polling

async def check_ingestion_status(self, ingestion_id: str) -> IngestionStatus:
    # Poll status of an async ingestion job
```

## Adding a Benchmark

1. Create `datasets/your_benchmark/`:

```
datasets/your_benchmark/
  __init__.py
  loader.py          # Raw data loading
  benchmark.py       # Benchmark class implementing the interface
  your_data.json     # Benchmark data file
```

2. Implement the Benchmark interface in `benchmark.py`:

```python
from runner.types import (
    Benchmark, BenchmarkConfig, QuestionFilter,
    QuestionTypeInfo, UnifiedQuestion, UnifiedSession, UnifiedMessage,
)

class YourBenchmark(Benchmark):
    name = "your-benchmark"

    async def load(self, config=None) -> None:
        # Load and parse your dataset
        # Build internal indexes: questions, sessions, question->session mapping

    def get_questions(self, filter=None) -> list[UnifiedQuestion]:
        # Return all questions, optionally filtered by type/limit

    def get_haystack_sessions(self, question_id: str) -> list[UnifiedSession]:
        # Return the sessions relevant to answering this specific question

    def get_ground_truth(self, question_id: str) -> str:
        # Return the expected answer

    def get_question_types(self) -> dict[str, QuestionTypeInfo]:
        # Return registry of question types with descriptions
```

3. Register in `datasets/base.py`:

```python
BENCHMARK_REGISTRY["your-benchmark"] = "datasets.your_benchmark.benchmark.YourBenchmark"
```

### Data Model

```
UnifiedSession
  session_id: str
  messages: list[UnifiedMessage]    # role="user"|"assistant", content, timestamp
  metadata: dict                     # date, speaker names, etc.

UnifiedQuestion
  question_id: str
  question: str                      # The question text
  question_type: str                 # Category (e.g., "temporal", "multi-session")
  ground_truth: str                  # Expected answer
  haystack_session_ids: list[str]    # Sessions containing the evidence
```

## Running Tests

```bash
pytest tests/
```

## Code Style

- Python: follows ruff defaults (88 char line length)
- TypeScript: follows Next.js conventions
- No docstrings required on internal functions — clear naming preferred
- Type hints on public interfaces

## Pull Request Process

1. **Fork** the repository on GitHub
2. **Clone** your fork: `git clone https://github.com/YOUR_USERNAME/memory_and_context_eval_harness.git`
3. **Create** a feature branch: `git checkout -b feature/your-feature`
4. **Make** your changes
5. **Test**: `pytest tests/`
6. **Build**: `cd frontend && npx next build`
7. **Push** to your fork: `git push origin feature/your-feature`
8. **Open a PR** against the `main` branch with a clear description of what and why

## Evaluation Pipeline Details

Understanding the pipeline helps when debugging or extending:

```
1. INGEST     Sessions loaded from benchmark → formatted → sent to provider
              Checkpoint: tracks which sessions are ingested per provider
              Modes: global (one container) or isolated (per-question containers)

2. SEARCH     For each question → provider.search(question, container_tag)
              Results saved to: data/runs/{runId}/results/{questionId}.json
              Checkpoint: per-question search status

3. ANSWER     Load search results → build prompt → call LLM
              System prompt: prompts/qa_agent.md
              Checkpoint: stores hypothesis per question

4. EVALUATE   Two parallel evaluations per question:
              a) judge_single() — scores answer vs ground truth (5 type-specific prompts)
              b) judge_retrieval_quality() — scores relevance of search results
              Checkpoint: score, label, explanation, retrieval metrics

5. REPORT     Aggregates all results into BenchmarkResult:
              accuracy, latency (p50/p95/p99), retrieval metrics (Hit@K, MRR, NDCG)
              Saved to: data/runs/{runId}/report.json
```

## Judge Prompts

The evaluation uses 5 specialized judge prompts based on question type:

| Question Type | Judge Behavior |
|---------------|----------------|
| Default | Standard factual comparison — is the key content present? |
| Temporal | Lenient on off-by-one day errors, equivalent date expressions |
| Knowledge Update | Must contain updated value to score above 0.5 |
| Preference | Rubric-based — does the answer reflect user preferences? |
| Adversarial | Correct behavior is to refuse or say "I don't know" |

## Questions?

Open an issue on GitHub or reach out to the maintainers.
