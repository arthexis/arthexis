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


def test_disable_default_site_for_public_mode(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, check=False):
        calls.append(cmd)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(services.subprocess, "run", fake_run)

    services._disable_default_site_for_public_mode(
        mode="public",
        allow_remove_default_site=True,
        sudo="sudo",
    )

    assert calls == [["sudo", "rm", "-f", "/etc/nginx/sites-enabled/default"]]


def test_apply_nginx_configuration_preserves_other_site_entries(monkeypatch, tmp_path: Path):
    """Regression: applying one Arthexis config must not remove unrelated nginx entries."""

    calls: list[list[str]] = []

    def fake_run(cmd, check=False):
        calls.append(cmd)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(services, "can_manage_nginx", lambda: True)
    monkeypatch.setattr(services, "ensure_nginx_in_path", lambda: True)
    monkeypatch.setattr(services.shutil, "which", lambda _: "/usr/sbin/nginx")
    monkeypatch.setattr(services, "generate_unified_config", lambda *_, **__: "server {}")
    monkeypatch.setattr(services, "_write_config_with_sudo", lambda *_, **__: None)
    monkeypatch.setattr(services, "_ensure_site_enabled", lambda *_, **__: None)
    monkeypatch.setattr(services, "_ensure_maintenance_assets", lambda **_: None)
    monkeypatch.setattr(services, "record_lock_state", lambda *_, **__: None)
    monkeypatch.setattr(services.subprocess, "run", fake_run)

    result = services.apply_nginx_configuration(
        mode="proxy",
        port=8000,
        role="web",
        https_enabled=False,
        include_ipv6=True,
        destination=tmp_path / "arthexis.conf",
        reload=False,
    )

    assert result.changed is True
    assert ["sudo", "rm", "-f", "/etc/nginx/sites-available/default"] not in calls
    assert ["sudo", "rm", "-f", "/etc/nginx/sites-enabled/default"] not in calls
    assert all(not (len(cmd) > 2 and cmd[1] == "rm") for cmd in calls)


def test_apply_nginx_configuration_uses_site_destination_when_provided(
    monkeypatch, tmp_path: Path
):
    write_calls: list[Path] = []
    enabled_calls: list[Path] = []

    monkeypatch.setattr(services, "can_manage_nginx", lambda: True)
    monkeypatch.setattr(services, "generate_unified_config", lambda *_, **__: "server {}")
    monkeypatch.setattr(
        services,
        "_write_config_with_sudo",
        lambda destination, *_args, **_kwargs: write_calls.append(destination),
    )
    monkeypatch.setattr(
        services,
        "_ensure_site_enabled",
        lambda destination, **_kwargs: enabled_calls.append(destination),
    )
    monkeypatch.setattr(services, "_ensure_maintenance_assets", lambda **_: None)
    monkeypatch.setattr(services, "record_lock_state", lambda *_, **__: None)

    services.apply_nginx_configuration(
        mode="proxy",
        port=8000,
        role="web",
        https_enabled=True,
        include_ipv6=True,
        destination=tmp_path / "primary.conf",
        site_destination=tmp_path / "managed.conf",
        reload=False,
    )

    assert write_calls == [tmp_path / "managed.conf"]
    assert enabled_calls == [tmp_path / "managed.conf"]

def test_apply_nginx_configuration_does_not_cleanup_on_render_error(monkeypatch, tmp_path: Path):
    """No destructive cleanup should run when unified rendering fails validation."""

    monkeypatch.setattr(services, "can_manage_nginx", lambda: True)
    monkeypatch.setattr(services, "record_lock_state", lambda *_, **__: None)
    monkeypatch.setattr(services, "generate_unified_config", lambda *_, **__: (_ for _ in ()).throw(ValueError("bad json")))

    with pytest.raises(services.ValidationError):
        services.apply_nginx_configuration(
            mode="proxy",
            port=8000,
            role="web",
            https_enabled=False,
            include_ipv6=True,
            destination=tmp_path / "arthexis.conf",
            site_config_path=tmp_path / "sites.json",
            reload=False,
        )
