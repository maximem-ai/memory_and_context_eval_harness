from typing import List, Dict, Optional, Any
import uuid
from adapters.base_adapter import MemoryAdapter

class YourMemoryAdapter(MemoryAdapter):
    """
    Sample implementation of a 'Smart' memory adapter.
    For this reference implementation, we use a simple keyword-based retrieval.
    Real implementations would likely use Vector DBs (Chroma/Pinecone/etc).
    """
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.store: Dict[str, List[Dict]] = {} # session_id -> list of memories

    def initialize(self) -> None:
        # e.g. connect to DB
        pass

    def write(self, session_id: str, turn_id: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        if session_id not in self.store:
            self.store[session_id] = []
        
        mem_id = str(uuid.uuid4())
        memory = {
            "memory_id": mem_id,
            "text": content,
            "turn_id": turn_id,
            "metadata": metadata or {}
        }
        self.store[session_id].append(memory)
        return mem_id

    def read(self, session_id: str, query: str, k: int = 5, metadata_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        # Simple keyword matching
        candidates = self.store.get(session_id, [])
        scored = []
        
        # Very naive overlap score
        query_words = set(query.lower().split())
        for m in candidates:
            text_words = set(m["text"].lower().split())
            overlap = len(query_words.intersection(text_words))
            score = overlap / len(query_words) if query_words else 0.0
            
            if score > 0:
                scored.append({
                    "memory_id": m["memory_id"],
                    "text": m["text"],
                    "score": score,
                    "metadata": m["metadata"]
                })
        
        # Sort by score desc
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]

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
