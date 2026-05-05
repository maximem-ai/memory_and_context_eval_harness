"""
Benchmark factory — instantiate benchmark loaders by name.
"""

from runner.types import Benchmark


BENCHMARK_REGISTRY = {
    "locomo": "datasets.locomo.benchmark.LoComoBenchmark",
    "longmemeval": "datasets.longmemeval.benchmark.LongMemEvalBenchmark",
    "dmr": "datasets.dmr.benchmark.DMRBenchmark",
}


def create_benchmark(name: str) -> Benchmark:
    """Instantiate a benchmark by name."""
    module_path = BENCHMARK_REGISTRY.get(name)
    if not module_path:
        raise ValueError(f"Unknown benchmark: {name}. Available: {list(BENCHMARK_REGISTRY.keys())}")

    import importlib
    mod_name, cls_name = module_path.rsplit(".", 1)
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name)
    return cls()
