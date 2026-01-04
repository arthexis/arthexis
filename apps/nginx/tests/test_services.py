from pathlib import Path

import pytest

from apps.nginx import services


def test_ensure_site_enabled_creates_symlink(monkeypatch, tmp_path: Path):
    sites_available = tmp_path / "sites-available"
    sites_enabled = tmp_path / "sites-enabled"
    sites_available.mkdir()
    source = sites_available / "arthexis.conf"
    source.write_text("test", encoding="utf-8")

    calls: list[tuple[list[str], bool]] = []

    def fake_run(cmd, check=False):
        calls.append((cmd, check))

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(services, "SITES_AVAILABLE_DIR", sites_available)
    monkeypatch.setattr(services, "SITES_ENABLED_DIR", sites_enabled)
    monkeypatch.setattr(services.subprocess, "run", fake_run)

    services._ensure_site_enabled(source, sudo="sudo")

    assert calls[0][0][:3] == ["sudo", "mkdir", "-p"]
    assert calls[0][0][3] == str(sites_enabled)
    assert calls[1][0][:3] == ["sudo", "ln", "-sf"]
    assert calls[1][0][3] == str(source)
    assert calls[1][0][4] == str(sites_enabled / source.name)


def test_ensure_site_enabled_skips_non_sites_available(monkeypatch, tmp_path: Path):
    sites_available = tmp_path / "sites-available"
    sites_enabled = tmp_path / "sites-enabled"
    sites_available.mkdir()
    source = tmp_path / "other" / "arthexis.conf"
    source.parent.mkdir()
    source.write_text("test", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(cmd, check=False):
        calls.append(cmd)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(services, "SITES_AVAILABLE_DIR", sites_available)
    monkeypatch.setattr(services, "SITES_ENABLED_DIR", sites_enabled)
    monkeypatch.setattr(services.subprocess, "run", fake_run)

    services._ensure_site_enabled(source, sudo="sudo")

    assert calls == []


def test_discover_secondary_instances(tmp_path: Path):
    primary = tmp_path / "primary"
    primary.mkdir()
    sibling = tmp_path / "blue"
    sibling.mkdir()
    lock_dir = sibling / ".locks"
    lock_dir.mkdir()
    (lock_dir / "backend_port.lck").write_text("9100", encoding="utf-8")
    (lock_dir / "role.lck").write_text("Watchtower", encoding="utf-8")

    instances = services.discover_secondary_instances(primary)

    assert len(instances) == 1
    instance = instances[0]
    assert instance.name == "blue"
    assert instance.port == 9100
    assert instance.role == "Watchtower"


def test_get_secondary_instance_missing(tmp_path: Path):
    base = tmp_path / "base"
    base.mkdir()

    with pytest.raises(services.SecondaryInstanceError):
        services.get_secondary_instance("missing", base_dir=base)


def test_apply_nginx_configuration_rejects_duplicate_ports(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(services, "can_manage_nginx", lambda: True)
    monkeypatch.setattr(services, "record_lock_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(services, "_write_config_with_sudo", lambda *args, **kwargs: None)
    monkeypatch.setattr(services, "_ensure_site_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(services, "apply_site_entries", lambda *args, **kwargs: False)
    monkeypatch.setattr(services, "ensure_nginx_in_path", lambda: False)
    monkeypatch.setattr(services.shutil, "which", lambda name: False)

    secondary = services.SecondaryInstance(name="blue", path=tmp_path, port=8888)

    with pytest.raises(services.ValidationError):
        services.apply_nginx_configuration(
            mode="internal",
            port=8888,
            role="Terminal",
            certificate=None,
            https_enabled=False,
            include_ipv6=False,
            external_websockets=True,
            destination=tmp_path / "nginx.conf",
            site_config_path=None,
            site_destination=None,
            reload=False,
            secondary_instance=secondary,
            sudo="sudo",
        )
