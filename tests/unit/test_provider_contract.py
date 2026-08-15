"""
Contract tests for the Provider interface.

Every adapter listed in PROVIDER_REGISTRY must import cleanly and satisfy the
Provider ABC. These tests deliberately avoid network calls and vendor SDKs:
adapters import their SDK lazily inside initialize(), so the contract can be
checked without credentials.

This file exists because a rename of the adapter base class once left several
adapters importing a class that no longer existed. CI did go red, but nothing
enforced it, so main stayed broken for months. Import and contract checks are
cheap; run them on every push and require them to pass before merge.
"""

import importlib
import inspect

import pytest

from adapters import PROVIDER_REGISTRY, create_provider
from runner.types import Provider

PROVIDER_NAMES = sorted(PROVIDER_REGISTRY)

# Every abstract coroutine an adapter is required to implement.
REQUIRED_METHODS = ("initialize", "ingest", "await_indexing", "search", "clear")


def _load(name: str) -> type:
    module_path, class_name = PROVIDER_REGISTRY[name].rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def test_registry_is_not_empty():
    assert PROVIDER_NAMES, "PROVIDER_REGISTRY must list at least one provider"


@pytest.mark.parametrize("name", PROVIDER_NAMES)
def test_registered_provider_imports(name):
    """The dotted path in the registry resolves to a real class."""
    cls = _load(name)
    assert inspect.isclass(cls)


@pytest.mark.parametrize("name", PROVIDER_NAMES)
def test_registered_provider_subclasses_provider(name):
    assert issubclass(_load(name), Provider)


@pytest.mark.parametrize("name", PROVIDER_NAMES)
def test_no_unimplemented_abstract_methods(name):
    """A provider that misses an abstract method cannot be instantiated."""
    missing = getattr(_load(name), "__abstractmethods__", frozenset())
    assert not missing, f"{name} does not implement: {sorted(missing)}"


@pytest.mark.parametrize("name", PROVIDER_NAMES)
@pytest.mark.parametrize("method", REQUIRED_METHODS)
def test_required_methods_are_coroutines(name, method):
    """The pipeline awaits these, so a sync def would break at runtime."""
    fn = getattr(_load(name), method, None)
    assert fn is not None, f"{name} is missing {method}()"
    assert inspect.iscoroutinefunction(fn), f"{name}.{method}() must be async"


@pytest.mark.parametrize("name", PROVIDER_NAMES)
def test_provider_declares_matching_name(name):
    """provider.name is what run artifacts are labelled with."""
    assert _load(name).name == name


@pytest.mark.parametrize("name", PROVIDER_NAMES)
def test_create_provider_instantiates_without_credentials(name):
    """Construction must not require an API key; initialize() does that."""
    instance = create_provider(name)
    assert isinstance(instance, Provider)


def test_create_provider_rejects_unknown_name():
    with pytest.raises(ValueError, match="Unknown provider"):
        create_provider("definitely-not-a-provider")
