# Quickstart

## Prerequisites

- Python 3.10 or newer
- An `OPENAI_API_KEY`. This drives the answer model and the LLM judge, and is
  needed whichever memory provider you are benchmarking.
- An API key for whichever provider you want to run.

## Install

```bash
git clone https://github.com/maximem-ai/memory_and_context_eval_harness.git
cd memory_and_context_eval_harness
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Configure

```bash
cp .env.example .env
```

Fill in `OPENAI_API_KEY` and the keys for the providers you plan to run. Keys
you leave blank simply mean that provider cannot be selected. Per-provider
settings such as `top_k` live in `configs/<provider>.yaml`, which reads secrets
from the environment using `${VAR}` placeholders.

## Get the datasets

```bash
python scripts/download_datasets.py --variant s
```

The harness evaluates against the official public distributions of LongMemEval
(`LongMemEval_S`, 500 questions) and LoCoMo (`locomo10`). Neither dataset is
bundled in this repository. See
[NOTES_ON_LICENSES.md](NOTES_ON_LICENSES.md) for their terms.

`--variant s` is the 500-question set the published results use, and is about
277 MB. The default, `--variant oracle`, is a much smaller set that is useful
for wiring up an adapter but is not comparable to published numbers. Pass
`--dataset locomo` or `--dataset longmemeval` to fetch only one.

## Run

```bash
python -m runner.server
```

This starts the API server and dashboard on `http://localhost:8766`. Choose a
provider, a benchmark, and a question count, then start the run.

To develop against the dashboard with hot reloading, run the frontend
separately:

```bash
cd frontend && npm install && npm run dev
```

Start with ten questions. It is enough to confirm that ingestion and retrieval
are wired up, and it costs a few cents rather than a few dollars.

## Where results go

Each run writes to `data/runs/<run-id>/`:

| File | Contents |
|---|---|
| `checkpoint.json` | Per-question state. An interrupted run resumes from here. |
| `report.json` | Accuracy, per-category breakdown, latency, retrieval metrics. |
| `results/` | Raw retrieval output for each question. |

These directories are gitignored.

## Docker

```bash
docker build -t eval-harness:latest .
docker run --rm -p 8766:8766 --env-file .env eval-harness:latest
```

## Adding your own memory system

See [BUILD_YOUR_OWN_ADAPTER.md](BUILD_YOUR_OWN_ADAPTER.md) for a walkthrough,
and [ADAPTER_SPEC.md](ADAPTER_SPEC.md) for the interface reference.

## Checking your changes

```bash
pytest tests            # unit and provider-contract tests
ruff check .            # correctness lint
```

Both run in CI on every pull request and must pass before merge.
