# Changelog

## [Unreleased]

### Added
- **LongMemEval-S runner for Synap**: `python -m benchmarks.longmemeval.run` ingests, retrieves, answers, judges and reports in one command, with resumable ingest checkpoints. See `docs/LONGMEMEVAL_S.md`.
- Upstream LongMemEval judge scripts vendored under `benchmarks/longmemeval/upstream/` (MIT), with a test that the judge prompts are byte-identical.
- Provider-agnostic LLM layer (LiteLLM) for the runner: `--answer-model` and `--judge-model` take `provider/model`.
- `requirements-longmemeval.txt` (pins `maximem-synap==0.5.8`) and `configs/synap_longmemeval_s.yaml`.

### Changed
- `adapters/synap_adapter.py`: retrieval retries with backoff and raises `SearchExhausted` instead of returning an empty list; a degraded empty response is retried; ingest retries transient 5xx but never re-sends after a read timeout; indexing polls are bounded by wall-clock time and treat a 404 status as terminal; `customer_id` is sent only to B2B instances; the gRPC listen stream can be turned off by config.
- `scripts/download_datasets.py` downloads to a `.part` file and renames on success, so an interrupted download is not mistaken for a finished one.

## [0.2.0] - 2026-05-05

### Added
- **LoCoMo benchmark**: full loader, benchmark, and answer prompt. Download via `python scripts/download_datasets.py --dataset locomo`.
- **Pipelined orchestrator**: per-question task pipeline that overlaps search → answer → evaluate stages, ~2.5× wall-time speedup over the strict-phase orchestrator. Opt in with `"pipelined": true` in the `/api/run` body.
- **Per-benchmark answer-prompt routing**: `prompts/{benchmark}_agent.md` (e.g., `prompts/locomo_agent.md`) takes precedence; falls back to `prompts/qa_agent.md` if absent. The legacy `prompts/system_prompt.md` is removed.
- **Binary judge methodology**: `prompts/judge.md` and `prompts/judge_temporal.md` produce strictly `0.0` or `1.0` scores. Matches the methodology used by mem0, Zep, and the original LoCoMo paper.
- **Temporal-specific judge**: `prompts/judge_temporal.md` with explicit ±1 day tolerance and relative-date-window handling.
- **Local prompt-iteration script**: `scripts/iterate_prompt.py` re-judges any saved run's retrieved context against a custom prompt, no provider re-fetch needed.
- **Per-question phase helpers**: `search_one`, `answer_one`, `evaluate_one` are now module-level callables in `runner/phases/{search,answer,evaluate}.py`.

### Changed
- Default config user_ids switched to `{benchmark}_bench_user` convention.
- `runner/orchestrator.py`: serial-phase path preserved as default; pipelined path opt-in.
- `prompts/judge.md`: replaced graded judge with binary v4 (industry-standard).
- `scorers/llm_judge.py`: judges are now loaded from disk per category (default `judge.md`, temporal `judge_temporal.md`); adversarial judge remains hardcoded.

### Removed
- `prompts/system_prompt.md` (superseded by `prompts/qa_agent.md` + per-benchmark routing).

## [0.1.0] - 2026-01-06

### Added
- **Core Engine**: Implemented `Runner`, `MemoryAdapter` interface, and `TraceLogger`.
- **Adapters**: Added `NoMemoryAdapter`, `FullTranscriptAdapter`, and sample `YourMemoryAdapter`.
- **Datasets**: Loaders for `LoCoMo` and `LongMemEval` (supports local files and embedded samples).
- **Scoring**: `ExactMatch/F1` and `TemporalConsistency` scorers.
- **CLI**: `evl` command for `ingest` and `run`.
- **Reproduction**: Docker support, `reproduce_locomo.ipynb` notebook.
- **CI**: GitHub Actions workflow for smoke testing.

### Design Trade-offs
- **PII Redaction**: Currently rudimentary; rely on dataset-level sanitization for now. Strict PII redaction (flags) is planned for v0.2.
- **External Integration**: The runner is standalone. It exposes a stable adapter interface that another platform can drive, but ships no integration shim of its own.
- **LLM Judge**: The implementation is a scaffold that requires an `OPENAI_API_KEY` to function fully. A dummy fallback is provided for smoke tests.
- **Dataset Hosting**: Loaders default to embedded small samples for immediate portability and smoke testing. Authenticated fetching is mocked.
