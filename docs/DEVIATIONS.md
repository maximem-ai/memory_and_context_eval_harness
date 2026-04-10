# Deviations from PRD

1. **PII Handling**: The PRD requests "Strip or flag PII by default". For the MVP, we assume input data is relatively safe or synthetic (LoCoMo/LongMemEval). Specific logic to detect and redact PII in traces hasn't been implemented to avoid introducing heavy NLP dependencies (like Presidio) in the core runner. This is deferred to v0.2.

2. **External Platform Integration**: The PRD states "merge with the external benchmark platform". As the external benchmark platform is external, we implemented a compatible runner that *can* be integrated, but operates standalone to ensure the deliverable is immediately runnable. The shim `bench/external_integration.py` exists for future expansion.

3. **Dataset Downloading**: To guarantee the "smoke test" works without network flakes or auth, the loaders default to an embedded sample set if the target file is missing. This ensures the Docker demo is robust. Real usage requires running `ingest` with valid paths/urls.
