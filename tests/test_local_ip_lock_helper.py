"""Tests for the local IP lock helper script."""

from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "local_ip_lock_helper", Path("scripts/helpers/local_ip_lock.py")
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_load_local_ip_lock_fallback_normalizes_addresses(tmp_path: Path) -> None:
    """Fallback lock loader should parse dict payloads and normalize values."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)
    lock_file = lock_dir / "local_ips.lck"
    lock_file.write_text(
        '{"addresses": [" 127.0.0.1 ", "[::1]", "bad", null, "127.0.0.1"]}',
        encoding="utf-8",
    )

    loaded = MODULE._load_local_ip_lock_fallback(tmp_path)

    assert loaded == {"127.0.0.1", "::1"}


def test_resolve_ip_helpers_uses_fallback_when_celery_missing(monkeypatch) -> None:
    """Helper resolution should gracefully fallback when celery import is missing."""

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "config.settings_helpers":
            raise ModuleNotFoundError("No module named 'celery'", name="celery")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    load_fn, discover_fn = MODULE._resolve_ip_helpers()

    assert load_fn is MODULE._load_local_ip_lock_fallback
    assert discover_fn is MODULE._discover_local_ip_addresses_fallback


def test_resolve_ip_helpers_reraises_unexpected_missing_module(monkeypatch) -> None:
    """Unexpected import errors should bubble up to avoid masking real issues."""

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "config.settings_helpers":
            raise ModuleNotFoundError("No module named 'totally_missing'", name="totally_missing")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    try:
        MODULE._resolve_ip_helpers()
    except ModuleNotFoundError as exc:
        assert exc.name == "totally_missing"
    else:
        raise AssertionError("Expected ModuleNotFoundError for unexpected missing module")
