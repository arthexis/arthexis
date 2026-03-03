"""Service helpers for optional liboqs integration."""

from __future__ import annotations

from importlib import import_module


class OqsImportError(RuntimeError):
    """Raised when python bindings for liboqs are unavailable."""


def get_oqs_module():
    """Return imported `oqs` module or raise a specific integration error."""

    try:
        return import_module("oqs")
    except ModuleNotFoundError as exc:
        raise OqsImportError(
            "The `oqs` package is not installed. Install liboqs Python bindings to enable this app."
        ) from exc


def discover_algorithms() -> dict[str, list[str]]:
    """Discover available KEM and signature algorithms from liboqs bindings."""

    oqs_module = get_oqs_module()
    return {
        "kem": sorted(oqs_module.get_enabled_kem_mechanisms()),
        "signature": sorted(oqs_module.get_enabled_sig_mechanisms()),
    }
