"""
Memory system provider adapters.

Each provider implements the Provider ABC from runner.types.
Use create_provider() to instantiate by name.
"""

from runner.types import Provider

PROVIDER_REGISTRY = {
    "synap": "adapters.synap_adapter.SynapProvider",
    "mem0": "adapters.mem0_adapter.Mem0Provider",
    "zep": "adapters.zep_adapter.ZepProvider",
    "supermemory": "adapters.supermemory_adapter.SupermemoryProvider",
}


def create_provider(name: str) -> Provider:
    """Instantiate a provider by name."""
    module_path = PROVIDER_REGISTRY.get(name)
    if not module_path:
        raise ValueError(f"Unknown provider: {name}. Available: {list(PROVIDER_REGISTRY.keys())}")

    mod_name, cls_name = module_path.rsplit(".", 1)
    import importlib
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name)
    return cls()
