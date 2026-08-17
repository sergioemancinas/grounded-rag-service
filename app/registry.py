"""Flat provider registries and the shared resolve() helper.

Each pipeline stage has one string-keyed dict of factories. Built-ins are
registered with the ``@register_*`` decorators (see app/providers.py and
app/grounding.py); there is deliberately no package scanning or plugin
framework, so ``grep register_embedder`` always shows every option.

Selection order in :func:`resolve`:

1. The dotted-path escape hatch (``EMBEDDER_CLASS`` and friends, imported by
   pydantic ``ImportString``) wins when set. Hatch classes execute
   operator-supplied code at startup; they must only ever come from the
   environment or .env, never from request data.
2. Otherwise the registry name from the matching ``*_PROVIDER`` setting.
   Unknown names fail fast, listing what is available.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from app.config import Settings


Factory = Callable[[Settings], Any]

EMBEDDERS: dict[str, Factory] = {}
GENERATORS: dict[str, Factory] = {}
RERANKERS: dict[str, Factory] = {}
STORES: dict[str, Factory] = {}
GROUNDING_JUDGES: dict[str, Factory] = {}


def _register(registry: dict[str, Factory], name: str) -> Callable[[Factory], Factory]:
    def decorator(factory: Factory) -> Factory:
        registry[name] = factory
        return factory

    return decorator


def register_embedder(name: str) -> Callable[[Factory], Factory]:
    """Register an Embedder factory under ``name`` (EMBEDDING_PROVIDER)."""
    return _register(EMBEDDERS, name)


def register_generator(name: str) -> Callable[[Factory], Factory]:
    """Register a Generator factory under ``name`` (GENERATION_PROVIDER)."""
    return _register(GENERATORS, name)


def register_reranker(name: str) -> Callable[[Factory], Factory]:
    """Register a Reranker factory under ``name``."""
    return _register(RERANKERS, name)


def register_store(name: str) -> Callable[[Factory], Factory]:
    """Register a Retriever/store factory under ``name``."""
    return _register(STORES, name)


def register_grounding_judge(name: str) -> Callable[[Factory], Factory]:
    """Register a GroundingJudge factory under ``name`` (GROUNDING_JUDGE)."""
    return _register(GROUNDING_JUDGES, name)


def resolve(
    name: str,
    hatch: Any,
    registry: dict[str, Factory],
    settings: Settings,
    kind: str,
) -> Any:
    """Resolve one stage implementation from a hatch class or a registry name.

    ``hatch`` is a class already imported by pydantic ``ImportString`` (or
    None). Hatch classes taking at least one parameter are constructed as
    ``cls(settings)``; zero-argument classes as ``cls()``. Registry factories
    always receive ``settings``.
    """
    if hatch is not None:
        try:
            parameters = inspect.signature(hatch).parameters
        except (TypeError, ValueError):
            parameters = {}
        if parameters:
            return hatch(settings)
        return hatch()
    factory = registry.get(name)
    if factory is None:
        raise KeyError(f"Unknown {kind} '{name}'. Available: {sorted(registry)}")
    return factory(settings)
