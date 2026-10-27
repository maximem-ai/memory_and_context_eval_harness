# Memory & Context Eval Harness

<h3>Benchmark any memory system on LongMemEval and LoCoMo, with settings you can read, change and re-run.</h3>

[![CI](https://github.com/maximem-ai/memory_and_context_eval_harness/actions/workflows/ci.yaml/badge.svg)](https://github.com/maximem-ai/memory_and_context_eval_harness/actions/workflows/ci.yaml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

[Quickstart](docs/QUICKSTART.md) · [LongMemEval-S runner](docs/LONGMEMEVAL_S.md) · [Build an adapter](docs/BUILD_YOUR_OWN_ADAPTER.md) · [Deviations](docs/DEVIATIONS.md) · [Results](https://github.com/maximem-ai/eval_benchmark_runs_output)

Memory benchmark numbers are hard to compare because every vendor runs them differently: different judge, different prompt, different subset. This harness runs Synap, Mem0, Zep, Supermemory, or a system you write yourself, through the same five steps with the same answer prompt and the same judge. Every setting is in the repo, and every place we differ from the published protocols is written down in [docs/DEVIATIONS.md](docs/DEVIATIONS.md).

| Benchmark | Scope | Provider | Result |
|---|---|---|---|
| LongMemEval | Answerable, excluding 3 dataset-defect questions | Synap | [**96.1%**](https://github.com/maximem-ai/eval_benchmark_runs_output) (449 of 467) |
| LongMemEval | 470 answerable questions (30 abstention excluded) | Synap | [**95.5%**](https://github.com/maximem-ai/eval_benchmark_runs_output) (449 of 470) |
| LongMemEval | All 500 questions | Synap | [**95.2%**](https://github.com/maximem-ai/eval_benchmark_runs_output) (476 of 500) |
| LongMemEval retrieval | Recall@20, 470 answerable questions | Synap | [**98.1%**](https://github.com/maximem-ai/eval_benchmark_runs_output) (461 of 470) |
| LoCoMo | 1,540 questions, categories 1 to 4 (adversarial excluded) | Synap | [**93.2%**](https://github.com/maximem-ai/eval_benchmark_runs_output) |

LongMemEval: `LongMemEval_S`, run 2026-10-13, single run, `gpt-5.4` answer model, `gpt-4o` judge with LongMemEval's own per-question-type prompts. LoCoMo: single run, `gpt-5-mini` as answer model and judge, binary CORRECT/WRONG. Recall@20 counts a question as a hit when `gpt-4o` judges at least one of the first 20 retrieved memories relevant, using the same relevance prompt Supermemory uses for its published 97% Recall@20, so the two are directly comparable in method. Per-question records, the answer prompt and the relevance labels are in the [results repo](https://github.com/maximem-ai/eval_benchmark_runs_output), together with each vendor's own published numbers, which were not re-run here. Details: [docs/DEVIATIONS.md](docs/DEVIATIONS.md).

## Contents

- [Quickstart](#quickstart)
- [Run LongMemEval-S against Synap from the command line](#run-longmemeval-s-against-synap-from-the-command-line)
- [How it works](#how-it-works)
- [Supported providers and benchmarks](#supported-providers-and-benchmarks)
- [Using a coding agent](#using-a-coding-agent)
- [Add your own provider or benchmark](#add-your-own-provider-or-benchmark)
- [Evaluation metrics](#evaluation-metrics)
- [Project structure](#project-structure)
- [Documentation](#documentation)

## Quickstart

You need Python 3.10 or newer, an `OPENAI_API_KEY` (it drives the answer model and the judge), and a key for the provider you want to test.

```bash
git clone https://github.com/maximem-ai/memory_and_context_eval_harness.git
cd memory_and_context_eval_harness
pip install -e .
cp .env.example .env                                  # add your keys
python scripts/download_datasets.py --variant s       # LongMemEval-S, about 277 MB
python -m runner.server                               # dashboard at http://localhost:8766
```

Open the dashboard, choose a provider and a benchmark, and start a run. Datasets are not bundled; see [Notes on licenses](docs/NOTES_ON_LICENSES.md).

If port 8766 is taken, set `EVAL_HARNESS_PORT=8767` for the server and `NEXT_PUBLIC_API_URL=http://localhost:8767` for the frontend. For frontend development with hot reload: `cd frontend && npm install && npm run dev`.

## Run LongMemEval-S against Synap from the command line

A standalone runner that uses the same settings as our published LongMemEval-S configuration. It needs no dashboard and no other providers. All options are in [docs/LONGMEMEVAL_S.md](docs/LONGMEMEVAL_S.md).

```bash
pip install -r requirements-longmemeval.txt      # pins maximem-synap==0.5.8
cp .env.example .env                             # set SYNAP_API_KEY, SYNAP_INSTANCE_ID,
                                                 # SYNAP_BASE_URL (optional) and ONE LLM key
python scripts/download_datasets.py --dataset longmemeval --variant s
python -m benchmarks.longmemeval.run --user-prefix mytry1_ --max-questions 3   # smoke test
python -m benchmarks.longmemeval.run --user-prefix mytry2_                     # full run
```

Results go to `results/longmemeval/<run_id>/`: `REPORT.md`, `summary.json`, `by_category.json`, `manifest.json` and `questions.jsonl`. Interrupted runs resume from `checkpoints/`.

Read this before a full run:

- **It is slow and costs money.** The S split has about 24,000 sessions (246,750 turns), and Synap indexes the sessions of one user one after another. The 444 questions outside the single-session-assistant type alone were estimated at about 50 hours at ingest concurrency 12. That is an estimate, not a timing of a complete run. Try `--max-questions 3` first.
- **Use a new `--user-prefix` for every fresh run.** Synap caches extraction by content hash, and the cache survives deleting a user's data. Re-ingesting under an old prefix can return an earlier extraction.
- **S scores are not comparable to scores from any other split**, such as M. Only the haystack size differs, so the question ids look the same. The runner refuses any file that is not exactly the S split.
- **The score depends on your Synap instance's own configuration** (extraction, deduplication, ranking). This repo does not set it. The aim is identical settings on our side, not a reproduced number.
- **The judge is `openai/gpt-4o` with LongMemEval's own prompt.** Any other judge makes the score not comparable to published LongMemEval numbers. The runner prints a warning and records it in the manifest.
- **The LongMemEval rows above use these same settings** (`gpt-5.4` answer, `gpt-4o` judge, accurate retrieval, source chunks and metadata on). The runner's own headline is the answerable-only figure; the all-500 figure is in `summary.json` under `all_questions`.

## How it works

```
Dataset ──> Ingest ──> Search ──> Answer ──> Evaluate ──> Report
              |           |          |           |           |
         Sessions     Per-query   LLM call   LLM judge   Accuracy,
         stored in    retrieval   generates  scores vs    latency,
         provider     from        hypothesis ground       retrieval
         memory       provider               truth        metrics
```

1. **Ingest** loads benchmark sessions into the memory provider.
2. **Search** retrieves context for each question.
3. **Answer** has an LLM answer using only that context.
4. **Evaluate** scores the answer with an LLM judge and computes retrieval metrics.
5. **Report** aggregates accuracy, latency and retrieval quality.

A provider adapter owns the first two steps. The harness owns the rest, so every system is scored the same way. Checkpoints are written atomically, search results are saved per question so you can re-run answer and evaluate without searching again, and runs resume after a crash. Pipeline internals are in [framework.md](framework.md).

## Supported providers and benchmarks

| Provider | Type | SDK |
|----------|------|-----|
| [Synap](https://maximem.ai) | Full context management | `pip install maximem-synap` |
| [Mem0](https://mem0.ai) | Memory extraction | `pip install mem0ai` |
| [Zep](https://getzep.com) | Temporal knowledge graph | `pip install zep-cloud` |
| [Supermemory](https://supermemory.ai) | Auto-chunking + search | `pip install supermemory` |
| **Yours** | Implement the `Provider` class | [Adapter spec](docs/ADAPTER_SPEC.md) |

| Benchmark | Questions | Focus |
|-----------|-----------|-------|
| [LongMemEval](https://arxiv.org/abs/2410.10813) (S and M splits) | 500 | Long-term memory across many sessions. S has 23,867 sessions. |
| [LoCoMo](https://snap-research.github.io/locomo/) | 1,540 (categories 1 to 4) | Very long multi-session conversations |
| **Yours** | Implement the `Benchmark` class | [Adding a benchmark](#add-your-own-provider-or-benchmark) |

## Using a coding agent

Paste this into Claude Code, Codex or Cursor:

> Clone https://github.com/maximem-ai/memory_and_context_eval_harness, run `pip install -e .`, copy `.env.example` to `.env`, run `python scripts/download_datasets.py --variant s`, then run `pytest tests -q` and tell me whether it passes.

For adding a provider, give the agent [docs/BUILD_YOUR_OWN_ADAPTER.md](docs/BUILD_YOUR_OWN_ADAPTER.md) and [docs/ADAPTER_SPEC.md](docs/ADAPTER_SPEC.md). `pytest tests/unit/test_provider_contract.py` checks the result.

## Add your own provider or benchmark

**A provider** implements the `Provider` class from `runner/types.py`:

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

Register it in `adapters/__init__.py`, then check it against the contract:

```python
PROVIDER_REGISTRY["my-provider"] = "adapters.my_provider.MyProvider"
```

```bash
pytest tests/unit/test_provider_contract.py -v
```

[Build Your Own Adapter](docs/BUILD_YOUR_OWN_ADAPTER.md) has a complete worked example, including the mistakes that quietly cost accuracy.

**A benchmark** implements `Benchmark` from `runner/types.py` (`load`, `get_questions`, `get_haystack_sessions`, `get_ground_truth`, `get_question_types`) and is registered in `datasets/base.py`:

```python
BENCHMARK_REGISTRY["my-benchmark"] = "datasets.my_benchmark.MyBenchmark"
```

## Evaluation metrics

Each question is searched in its own container, holding only that question's haystack sessions.

Every run also computes Hit@K, Precision@K, Recall@K, F1@K, MRR and NDCG over the retrieved context.

## Project structure

```
runner/         Pipeline orchestrator, phases, API server
adapters/       Provider implementations
datasets/       Benchmark loaders and data files
benchmarks/     Standalone LongMemEval-S runner for Synap
scorers/        LLM judge and retrieval metrics
prompts/        Answering and judging prompts
configs/        Provider and orchestrator YAML
frontend/       Next.js dashboard
tests/          Unit tests (no network, no vendor SDKs)
```

Provider credentials live in `.env`. Provider settings are in `configs/*.yaml`. Orchestrator configs for multi-dataset pipelines are in `configs/orchestrator_*.yaml`.

## Documentation

- [Quickstart](docs/QUICKSTART.md): install, configure, download datasets, run
- [LongMemEval-S runner](docs/LONGMEMEVAL_S.md): the command-line runner and every setting
- [Build Your Own Adapter](docs/BUILD_YOUR_OWN_ADAPTER.md): benchmark your own memory system
- [Provider Specification](docs/ADAPTER_SPEC.md): methods, types, lifecycle
- [Framework Architecture](framework.md): phases, checkpointing, judge prompts, metrics
- [Deviations](docs/DEVIATIONS.md): where this setup differs from the published protocols
- [Contributing](CONTRIBUTING.md): add providers and benchmarks, open a PR

## License

MIT. The datasets carry their own terms; see [Notes on licenses](docs/NOTES_ON_LICENSES.md).
