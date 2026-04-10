import pytest
from adapters.adapter_examples.adapter_no_memory import NoMemoryAdapter
from adapters.adapter_examples.adapter_full_transcript import FullTranscriptAdapter
from adapters.adapter_examples.adapter_yourmemory import YourMemoryAdapter

def test_no_memory_adapter():
    adapter = NoMemoryAdapter({})
    adapter.initialize()
    
    # Write something
    mid = adapter.write("s1", "t1", "Hello")
    assert mid is not None
    
    # Read should be empty
    memories = adapter.read("s1", "Hello")
    assert len(memories) == 0

def test_full_transcript_adapter():
    adapter = FullTranscriptAdapter({})
    
    adapter.write("s1", "t1", "Hello world")
    adapter.write("s1", "t2", "Goodbye world")
    
    # Read should return all
    memories = adapter.read("s1", "query")
    assert len(memories) == 2
    assert memories[0]["text"] == "Hello world"
    assert memories[1]["text"] == "Goodbye world"
    
    # Isolation
    adapter.write("s2", "t1", "Other session")
    s1_mems = adapter.read("s1", "query")
    assert len(s1_mems) == 2
    
    s2_mems = adapter.read("s2", "query")
    assert len(s2_mems) == 1

def test_your_memory_adapter():
    adapter = YourMemoryAdapter({})
    
    adapter.write("s1", "t1", "The apple is red")
    adapter.write("s1", "t2", "The sky is blue")
    
    # Query for apple
    res = adapter.read("s1", "apple")
    assert len(res) >= 1
    assert "apple" in res[0]["text"]
    
    # Query for sky
    res = adapter.read("s1", "sky")
    assert len(res) >= 1
    assert "sky" in res[0]["text"]
