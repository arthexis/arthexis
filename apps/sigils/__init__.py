from __future__ import annotations

__all__ = ["resolve"]


def _get_resolver_module():
    from . import sigil_resolver

    return sigil_resolver


def resolve(value: str | None, default: str = "") -> str:
    """Resolve a sigil token while providing a sensible fallback."""

    resolver = _get_resolver_module()
    resolved = resolver.resolve_sigils(value or "")
    cleaned = (resolved or "").strip()
    if not cleaned:
        return default
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return default
    return cleaned
