# Changelog

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
- **External Integration**: Implemented as a standalone compatible shim. Full integration with the upstream external benchmark platform would require it to be published to PyPI.
- **LLM Judge**: The implementation is a scaffold that requires an `OPENAI_API_KEY` to function fully. A dummy fallback is provided for smoke tests.
- **Dataset Hosting**: Loaders default to embedded small samples for immediate portability and smoke testing. Authenticated fetching is mocked.
