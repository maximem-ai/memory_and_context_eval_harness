# Memory Adapter Specification

The `MemoryAdapter` interface is the core contract for plugging memory systems into the Eval Framework.

## Python Contract

Adapters must inherit from `adapters.base_adapter.MemoryAdapter` and implement:

- `write(session_id, turn_id, content, metadata) -> memory_id`
- `read(session_id, query, k, metadata_filters) -> List[Dict]`
- `delete(memory_id) -> bool`
- `list(session_id, limit) -> List[Dict]`
- `flush() -> None`

## HTTP / JSON Contract (for Hosted Providers)

Adapters can also be accessed via HTTP. A generic HTTP wrapper can be used if the provider implements:

### POST /v1/memory/write
Request:
```json
{
  "session_id": "...",
  "turn_id": "...",
  "content": "...",
  "metadata": {}
}
```
Response:
```json
{
  "memory_id": "..."
}
```

### POST /v1/memory/read
Request:
```json
{
  "session_id": "...",
  "query": "...",
  "k": 5
}
```
Response:
```json
{
  "memories": [
    {
      "memory_id": "...",
      "text": "...",
      "score": 0.9,
      "metadata": {}
    }
  ]
}
```

This allows non-Python memory systems to be benchmarked using a thin Python wrapper client.
