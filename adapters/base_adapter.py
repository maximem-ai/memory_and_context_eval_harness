"""
Base provider interface for memory system adapters.

All providers must implement the Provider ABC defined in runner.types.
This module re-exports it for convenience.
"""

from runner.types import (  # noqa: F401
    Provider,
    ProviderConfig,
    IngestOptions,
    IngestResult,
    SearchOptions,
    IngestionStatus,
    IndexingProgress,
    IndexingProgressCallback,
    SessionIngestion,
    ProviderPrompts,
    ConcurrencyConfig,
    UnifiedSession,
    UnifiedMessage,
)
