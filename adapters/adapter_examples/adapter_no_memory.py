from typing import List, Dict, Optional, Any
import uuid
from adapters.base_adapter import MemoryAdapter

class NoMemoryAdapter(MemoryAdapter):
    """
    Baseline adapter that stores nothing and retrieves nothing.
    Represents a stateless system.
    """
    def initialize(self) -> None:
        pass

    def write(self, session_id: str, turn_id: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        # We pretend to write but store nothing.
        return str(uuid.uuid4())

    def read(self, session_id: str, query: str, k: int = 5, metadata_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        # Always return empty context
        return []

    def delete(self, memory_id: str) -> bool:
        return False

    def list(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        return []

    def flush(self) -> None:
        pass
