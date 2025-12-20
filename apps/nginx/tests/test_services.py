from pathlib import Path

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
