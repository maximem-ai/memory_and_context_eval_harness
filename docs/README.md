# Eval Framework

A production-grade, reproducible benchmarking framework for memory/context systems.

## Features

- **Standardized Harness**: Powered by standardized evaluation methodology.
- **Reference Adapters**: No Memory, Full Transcript, and "YourMemory" sample.
- **Datasets**: Native support for [LongMemEval](https://github.com/xiaowu0162/LongMemEval) and [LoCoMo](https://github.com/snap-research/locomo).
- **Reproducibility**: Dockerized runs, Git-SHA tracking, and full artifact generation (`trace.json`, `metrics.csv`).
- **Flexible Scoring**: Exact/F1, Temporal Accuracy, and LLM-as-Judge.

## Quickstart

### 1. Installation

```bash
git clone https://github.com/maximem-ai/memory_and_context_eval_harness.git
cd memory_and_context_eval_harness
pip install -e .
```

### 2. Run a Smoke Test

```bash
# Run with NoMemory adapter on LoCoMo
evl run --config examples/longmemeval_demo.yaml
```

### 3. Docker

```bash
docker build -t eval-fw:latest .
docker run --rm eval-fw:latest evl run --config examples/longmemeval_demo.yaml
```

See [QUICKSTART.md](QUICKSTART.md) for more details.

## Documentation

- [Quickstart Guide](QUICKSTART.md)
- [Adapter Specification](ADAPTER_SPEC.md)
- [Deviations from Published Methodology](DEVIATIONS.md)
- [Notes on Licenses](NOTES_ON_LICENSES.md)

## License

MIT
