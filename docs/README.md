# Documentation

An open evaluation harness for memory and context management systems. It runs
standardised benchmarks against pluggable providers and reports accuracy,
latency and retrieval quality.

## Start here

| Guide | Read it when |
|---|---|
| [Quickstart](QUICKSTART.md) | You want to install the harness and run an existing provider. |
| [Build Your Own Adapter](BUILD_YOUR_OWN_ADAPTER.md) | You want to benchmark your own memory system. |
| [Provider Specification](ADAPTER_SPEC.md) | You need the exact interface: methods, types, lifecycle. |
| [Deviations](DEVIATIONS.md) | You are comparing our numbers against a published paper. |
| [Notes on Licenses](NOTES_ON_LICENSES.md) | You need dataset terms of use. |

For pipeline internals (phases, checkpointing, evaluation modes, judge prompts,
retrieval metrics) see [framework.md](../framework.md) at the repository root.

## What the harness does

```
Dataset ──> Ingest ──> Search ──> Answer ──> Evaluate ──> Report
              |           |          |           |           |
         Sessions     Per-query   LLM call   LLM judge   Accuracy,
         stored in    retrieval   generates  scores vs    latency,
         provider     from        hypothesis ground       retrieval
         memory       provider               truth        metrics
```

A provider adapter is responsible for the first two phases only: storing
conversations, and retrieving relevant context for a question. The harness owns
answering, judging and reporting, so every system is scored the same way.

## Supported providers

Synap, Mem0, Zep and Supermemory ship in `adapters/`. Adding your own is
documented in [Build Your Own Adapter](BUILD_YOUR_OWN_ADAPTER.md).

## Published results

Benchmark results, full methodology and the cross-vendor configuration
comparison live in
[`maximem-ai/eval_benchmark_runs_output`](https://github.com/maximem-ai/eval_benchmark_runs_output).

## License

MIT. Datasets carry their own terms, see [NOTES_ON_LICENSES.md](NOTES_ON_LICENSES.md).
