# Quickstart Guide

## Prerequisites

- Python 3.10+
- Docker (optional but recommended)

## Running Locally

1. **Install Dependencies**
   ```bash
   pip install -e .[dev]
   ```

2. **Ingest Data** (Optional - framework uses embedded samples if not found)
   ```bash
   evl ingest --dataset longmemeval --out data/longmemeval
   ```

3. **Run Benchmark**
   ```bash
   evl run --config examples/longmemeval_demo.yaml
   ```

   This will create a `results/run-<uuid>` directory with:
   - `predictions.jsonl`: Detailed predictions
   - `metrics.csv`: Summary metrics
   - `trace.json`: Detailed execution trace
   - `metadata.json`: Run configuration and environment info

## Running with Docker

To ensure reproducibility, use the Docker image:

```bash
docker build -t eval-fw:latest .
docker run --env OPENAI_API_KEY=$OPENAI_API_KEY eval-fw:latest evl run --config examples/longmemeval_demo.yaml
```

## Adding a New Adapter

1. Create a class inheriting from `MemoryAdapter`.
2. Implement `write`, `read`, `delete`, `list`, `flush`.
3. Reference it in your config YAML: `adapter: my_module.MyAdapter`.

See [ADAPTER_SPEC.md](ADAPTER_SPEC.md) for details.
