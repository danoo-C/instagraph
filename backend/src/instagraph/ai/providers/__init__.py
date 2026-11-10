"""AI providers, discovered automatically.

Every module in this package with a module-level ``provider`` object is registered,
the same way ``api/routes`` modules are included. The public build ships only the
Anthropic API provider; a private, git-ignored ``*_local.py`` module (docs/ai-assistant.md
A8.9) is picked up the same way when present, so public code never names it.

Development: ``INSTAGRAPH_AI_FAKE=1`` adds a scripted fake provider, so the AI page can
be tried without any real provider. It never sends anything anywhere.
"""

import importlib
import logging
import os
import pkgutil
import threading

from instagraph.ai.providers.base import Provider

logger = logging.getLogger("instagraph")

_SKIP = frozenset({"base", "fake"})
_registry: dict[str, Provider] | None = None
_lock = threading.Lock()


def _discover() -> dict[str, Provider]:
    found: dict[str, Provider] = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name in _SKIP or info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{__name__}.{info.name}")
        except Exception:  # a broken optional provider must not break the app
            logger.warning("AI provider module %s failed to load", info.name, exc_info=True)
            continue
        provider = getattr(module, "provider", None)
        if provider is not None and getattr(provider, "id", None):
            found[provider.id] = provider
    if os.environ.get("INSTAGRAPH_AI_FAKE") == "1":
        from instagraph.ai.providers.fake import demo_provider

        found["fake"] = demo_provider()
    return found


def providers() -> dict[str, Provider]:
    """``provider id -> provider`` (discovered once per process)."""
    global _registry
    with _lock:
        if _registry is None:
            _registry = _discover()
        return dict(_registry)


def get_provider(provider_id: str) -> Provider | None:
    return providers().get(provider_id)


def register(provider: Provider) -> None:
    """Add or replace a provider (tests, and the fake provider)."""
    global _registry
    with _lock:
        if _registry is None:
            _registry = _discover()
        _registry[provider.id] = provider


def reset() -> None:
    """Forget registered providers; the next call re-discovers (tests)."""
    global _registry
    with _lock:
        _registry = None
