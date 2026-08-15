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
| [LongMemEval](https://arxiv.org/abs/2410.10813) | 500 | 940 | Long-term memory across sessions |
| [LoCoMo](https://snap-research.github.io/locomo/) | 1,540 | 5,290 | Multi-conversation, multi-modal, very long context |
| **Custom** | Implement `Benchmark` ABC | See [Adding Benchmarks](#adding-a-benchmark) |

## Headline Results

Run with `gpt-5-mini` answer + `gpt-5-mini` judge, binary judging methodology (CORRECT / WRONG, 5-seed mean), excluding adversarial questions per industry convention (mem0, Zep, original LoCoMo paper). Both benchmarks are scored at full scale on the official public distributions, with no custom subsets and no relabeling.

| Benchmark | Scope | Provider | Accuracy |
|---|---|---|---|
| LoCoMo | Full set, 1,540 Cat 1-4 questions (adversarial Cat 5 excluded) | [Synap](https://maximem.ai) | **93.2%** |
| LongMemEval | Full set, 500 questions across 6 categories | [Synap](https://maximem.ai) | **92.0%** |

Full methodology, category-level breakdowns, and the cross-vendor comparison live in the results repo: [`maximem-ai/eval_benchmark_runs_output`](https://github.com/maximem-ai/eval_benchmark_runs_output).

## Quickstart

### 1. Install

```bash
git clone https://github.com/maximem-ai/memory_and_context_eval_harness.git
cd memory_and_context_eval_harness
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Get the datasets

```bash
python scripts/download_datasets.py --variant s
```

`--variant s` is the 500-question LongMemEval set used for the published results. See [docs/QUICKSTART.md](docs/QUICKSTART.md) for the other options.

### 4. Run

```bash
python -m runner.server
```

This starts the API server and dashboard at `http://localhost:8766`.

For frontend development with hot reloading, run it separately:

```bash
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

Then check your class against the contract before running anything:

```bash
pytest tests/unit/test_provider_contract.py -v
```

**→ [Build Your Own Adapter](docs/BUILD_YOUR_OWN_ADAPTER.md)** walks through the whole thing with a complete worked example, including the mistakes that quietly cost you accuracy. **[Provider Specification](docs/ADAPTER_SPEC.md)** is the interface reference.

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

- [Quickstart](docs/QUICKSTART.md) — Install, configure, download datasets, run
- [Build Your Own Adapter](docs/BUILD_YOUR_OWN_ADAPTER.md) — Benchmark your own memory system, with a worked example
- [Provider Specification](docs/ADAPTER_SPEC.md) — Interface reference: methods, types, lifecycle
- [Framework Architecture](framework.md) — Pipeline phases, checkpointing, evaluation modes, judge prompts, retrieval metrics
- [Deviations](docs/DEVIATIONS.md) — Where our setup differs from published methodology
- [Contributing Guide](CONTRIBUTING.md) — How to add providers, benchmarks, and submit PRs

## License

MIT
