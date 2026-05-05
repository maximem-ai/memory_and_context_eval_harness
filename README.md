# Memory & Context Eval Harness

An open-source evaluation platform for benchmarking memory and context management systems. Compare providers like Synap, Mem0, Zep, and Supermemory across standardized benchmarks with a 5-phase pipeline and interactive dashboard.

## Architecture

```
Dataset ──> Ingest ──> Search ──> Answer ──> Evaluate ──> Report
              |           |          |           |           |
         Sessions     Per-query   LLM call   LLM judge   Accuracy,
         stored in    retrieval   generates  scores vs    latency,
         provider     from        hypothesis ground       retrieval
         memory       provider               truth        metrics
```

**Pipeline Phases:**
1. **Ingest** — Load benchmark sessions into a memory provider
2. **Search** — Retrieve relevant context for each question
3. **Answer** — Generate answers using an LLM with retrieved context
4. **Evaluate** — Score answers with an LLM judge + compute retrieval metrics
5. **Report** — Aggregate accuracy, latency, and retrieval quality

## Supported Providers

| Provider | Type | SDK |
|----------|------|-----|
| [Synap](https://maximem.ai) | Full context management | `pip install maximem-synap` |
| [Mem0](https://mem0.ai) | Memory extraction | `pip install mem0ai` |
| [Zep](https://getzep.com) | Temporal knowledge graph | `pip install zep-cloud` |
| [Supermemory](https://supermemory.ai) | Auto-chunking + search | `pip install supermemory` |
| **Custom** | Implement `Provider` ABC | See [Adapter Spec](docs/ADAPTER_SPEC.md) |

## Supported Benchmarks

| Benchmark | Questions | Sessions | Focus |
|-----------|-----------|----------|-------|
| [LongMemEval](https://arxiv.org/abs/2407.01501) | 500 | 940 | Long-term memory across sessions |
| [LoCoMo](https://snap-research.github.io/locomo/) | 1,540 | 5,290 | Multi-conversation, multi-modal, very long context |
| **Custom** | Implement `Benchmark` ABC | See [Adding Benchmarks](#adding-a-benchmark) |

## Headline Results

Run with `gpt-5` answer + `gpt-5-mini` judge, binary judging methodology (CORRECT / WRONG, 5-seed mean), excluding adversarial questions per industry convention (mem0, Zep, original LoCoMo paper).

| Benchmark | Provider | Cat 1-4 |
|---|---|---|
| LoCoMo | [Synap](https://maximem.ai) | **93.2%** |
| LongMemEval (50q) | [Synap](https://maximem.ai) | **92.0%** (also reproduced on mem0's `memory-benchmarks` harness after `_flatten_context` fix lands) |

See [docs/methodology.md](docs/methodology.md) (TBD) for run configuration, retrieval mode, and reproducibility notes.

## Quickstart

### 1. Install

```bash
git clone https://github.com/gauravmaximem/memory_and_context_eval_harness.git
cd memory_and_context_eval_harness
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run (Backend + Frontend)

```bash
python -m runner.server --with-frontend
```

This starts the API server at `http://localhost:8766`, launches the frontend, and opens the dashboard at `http://localhost:3000` automatically.

Or run them separately:

```bash
# Backend only
python -m runner.server

# Frontend only (in another terminal)
cd frontend && npm install && npm run dev
```

## Evaluation Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Global** | All sessions in one shared container | Fast, cross-session reasoning |
| **Isolated** | Separate container per question | Clean evaluation, no cross-contamination |

## Retrieval Metrics

Every evaluation run computes:
- **Hit@K** — Is relevant context in top-K results?
- **Precision@K** — Fraction of relevant results in top-K
- **Recall@K** — Fraction of relevant results found
- **F1@K** — Harmonic mean of precision and recall
- **MRR** — Mean Reciprocal Rank
- **NDCG** — Normalized Discounted Cumulative Gain

## Adding a Provider

Implement the `Provider` abstract class from `runner/types.py`:

```python
from runner.types import Provider, ProviderConfig, IngestOptions, IngestResult, SearchOptions

class MyProvider(Provider):
    name = "my-provider"

    async def initialize(self, config: ProviderConfig) -> None: ...
    async def ingest(self, sessions, options: IngestOptions) -> IngestResult: ...
    async def await_indexing(self, result, container_tag, on_progress=None) -> None: ...
    async def search(self, query: str, options: SearchOptions) -> list: ...
    async def clear(self, container_tag: str) -> None: ...
```

Register it in `adapters/__init__.py`:

```python
PROVIDER_REGISTRY["my-provider"] = "adapters.my_provider.MyProvider"
```

## Adding a Benchmark

Implement the `Benchmark` abstract class from `runner/types.py`:

```python
from runner.types import Benchmark, UnifiedQuestion, UnifiedSession

class MyBenchmark(Benchmark):
    name = "my-benchmark"

    async def load(self, config=None) -> None: ...
    def get_questions(self, filter=None) -> list[UnifiedQuestion]: ...
    def get_haystack_sessions(self, question_id: str) -> list[UnifiedSession]: ...
    def get_ground_truth(self, question_id: str) -> str: ...
    def get_question_types(self) -> dict: ...
```

Register it in `datasets/base.py`:

```python
BENCHMARK_REGISTRY["my-benchmark"] = "datasets.my_benchmark.MyBenchmark"
```

## Configuration

Provider credentials are stored in `.env`. Provider-specific settings are in `configs/*.yaml`. Orchestrator configs define multi-dataset pipelines in `configs/orchestrator_*.yaml`.

## Project Structure

```
runner/              # Core pipeline orchestrator + phases
  phases/            # Ingest, search, answer, evaluate, report
adapters/            # Provider implementations
datasets/            # Benchmark loaders + data files
scorers/             # LLM judge + retrieval metrics
prompts/             # System prompts for answering + judging
configs/             # Provider + orchestrator YAML configs
frontend/            # Next.js dashboard
```

## Documentation

- [Framework Architecture](framework.md) — Pipeline phases, checkpointing, evaluation modes, judge prompts, retrieval metrics
- [Contributing Guide](CONTRIBUTING.md) — How to add providers, benchmarks, and submit PRs
- [Adapter Specification](docs/ADAPTER_SPEC.md) — Provider interface details

## License

MIT
