from bench.config import Config
from bench.runner import Runner
import os
import shutil

def test_runner_end_to_end():
    # Setup dummy config
    cfg = Config(
        dataset="locomo",
        adapter="adapters.adapter_examples.adapter_no_memory.NoMemoryAdapter",
        scorer="exact_f1",
        output_dir="tests/test_output",
        adapter_config={},
        llm_config={}
    )
    
    if os.path.exists(cfg.output_dir):
        shutil.rmtree(cfg.output_dir)
        
    runner = Runner(cfg)
    out_dir = runner.run()
    
    assert os.path.exists(out_dir)
    assert os.path.exists(os.path.join(out_dir, "metadata.json"))
    assert os.path.exists(os.path.join(out_dir, "predictions.jsonl"))
    
    # Cleanup
    shutil.rmtree(cfg.output_dir)
