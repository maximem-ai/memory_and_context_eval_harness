from typing import List, Dict, Optional, Any
import uuid
from adapters.base_adapter import MemoryAdapter

class FullTranscriptAdapter(MemoryAdapter):
    """
    Baseline adapter that returns the full conversation history.
    """
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.store: Dict[str, List[Dict]] = {} # session_id -> list of memories

    def initialize(self) -> None:
        pass

    def write(self, session_id: str, turn_id: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        if session_id not in self.store:
            self.store[session_id] = []
        
        mem_id = str(uuid.uuid4())
        memory = {
            "memory_id": mem_id,
            "text": content,
            "turn_id": turn_id,
            "metadata": metadata or {},
            "timestamp": "N/A" # In real imp, add timestamp
        }
        self.store[session_id].append(memory)
        return mem_id

    def read(self, session_id: str, query: str, k: int = 5, metadata_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        # Return all memories for the session
        # Score is dummy 1.0
        res = []
        for m in self.store.get(session_id, []):
            res.append({
                "memory_id": m["memory_id"],
                "text": m["text"],
                "score": 1.0,
                "metadata": m["metadata"]
            })
        return res

    def delete(self, memory_id: str) -> bool:
        for session_id, memories in self.store.items():
            for i, m in enumerate(memories):
                if m["memory_id"] == memory_id:
                    self.store[session_id].pop(i)
                    return True
        return False

    def list(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self.store.get(session_id, [])[-limit:]

    def flush(self) -> None:
        self.store = {}
