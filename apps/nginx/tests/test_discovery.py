from pathlib import Path

import pytest

from apps.nginx import services
from apps.nginx.discovery import _discover_site_config_paths, _read_int_lock, _resolve_site_destination


@pytest.mark.critical
def test_read_int_lock_uses_fallback_for_invalid_values(tmp_path: Path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "backend_port.lck").write_text("not-a-port", encoding="utf-8")

    assert _read_int_lock(lock_dir, "backend_port.lck", 8888) == 8888


@pytest.mark.critical
def test_discover_site_config_paths_prefers_enabled_then_available(monkeypatch, tmp_path: Path):
    enabled_dir = tmp_path / "enabled"
    available_dir = tmp_path / "available"
    enabled_dir.mkdir()
    available_dir.mkdir()

    monkeypatch.setattr(services, "SITES_ENABLED_DIR", enabled_dir)
    monkeypatch.setattr(services, "SITES_AVAILABLE_DIR", available_dir)

    from_enabled = enabled_dir / "arthexis-main.conf"
    from_enabled.write_text("server {}", encoding="utf-8")
    ignored = enabled_dir / "arthexis-sites.conf"
    ignored.write_text("server {}", encoding="utf-8")

    paths = _discover_site_config_paths(None)
    assert paths == [from_enabled]

    from_enabled.unlink()
    from_available = available_dir / "arthexis-fallback.conf"
    from_available.write_text("server {}", encoding="utf-8")
    assert _discover_site_config_paths(None) == [from_available]


@pytest.mark.critical
def test_resolve_site_destination_prefers_enabled_path(monkeypatch, tmp_path: Path):
    enabled_dir = tmp_path / "enabled"
    available_dir = tmp_path / "available"
    enabled_dir.mkdir()
    available_dir.mkdir()

    monkeypatch.setattr(services, "SITES_ENABLED_DIR", enabled_dir)
    monkeypatch.setattr(services, "SITES_AVAILABLE_DIR", available_dir)

    available_path = available_dir / "arthexis-sites.conf"
    available_path.write_text("# available", encoding="utf-8")
    assert _resolve_site_destination() == str(available_path)

    enabled_path = enabled_dir / "arthexis-sites.conf"
    enabled_path.write_text("# enabled", encoding="utf-8")
    assert _resolve_site_destination() == str(enabled_path)
