import tomllib
from importlib import import_module
from pathlib import Path

import pytest

from apps.core.system import lifecycle


def test_layout_defaults_to_opt(monkeypatch):
    monkeypatch.delenv("ARTHEXIS_INSTALL_ROOT", raising=False)
    monkeypatch.delenv("ARTHEXIS_MANAGED_ROOT", raising=False)

    current = lifecycle.layout()

    assert current.root == Path("/opt/arthexis")
    assert current.checkout == Path("/opt/arthexis/app")


def test_layout_accepts_install_root_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTHEXIS_INSTALL_ROOT", str(tmp_path))

    current = lifecycle.layout()

    assert current.root == tmp_path
    assert current.checkout == tmp_path / "app"


def test_layout_accepts_legacy_environment_override(monkeypatch, tmp_path):
    monkeypatch.delenv("ARTHEXIS_INSTALL_ROOT", raising=False)
    monkeypatch.setenv("ARTHEXIS_MANAGED_ROOT", str(tmp_path))

    current = lifecycle.layout()

    assert current.root == tmp_path


def test_install_root_override_precedes_legacy_override(monkeypatch, tmp_path):
    legacy_root = tmp_path / "legacy"
    install_root = tmp_path / "install"
    monkeypatch.setenv("ARTHEXIS_MANAGED_ROOT", str(legacy_root))
    monkeypatch.setenv("ARTHEXIS_INSTALL_ROOT", str(install_root))

    current = lifecycle.layout()

    assert current.root == install_root


def test_prepare_runs_application_owned_steps(monkeypatch, tmp_path):
    current = lifecycle.InstallationLayout(
        root=tmp_path,
        checkout=tmp_path / "app",
    )
    current.checkout.mkdir()
    calls = []

    monkeypatch.setattr(
        lifecycle,
        "migrate",
        lambda *, layout: calls.append("migrate"),
    )
    monkeypatch.setattr(
        lifecycle,
        "collectstatic",
        lambda *, layout: calls.append("collectstatic"),
    )

    result = lifecycle.prepare(layout=current)

    assert result == current
    assert calls == ["migrate", "collectstatic"]


def test_prepare_requires_checkout(tmp_path):
    current = lifecycle.InstallationLayout(
        root=tmp_path,
        checkout=tmp_path / "missing",
    )

    with pytest.raises(FileNotFoundError):
        lifecycle.prepare(layout=current)


def test_install_and_upgrade_are_semantic_prepare_hooks(monkeypatch, tmp_path):
    current = lifecycle.InstallationLayout(
        root=tmp_path,
        checkout=tmp_path / "app",
    )
    calls = []

    def fake_prepare(*, layout, **_kwargs):
        calls.append(layout)
        return layout

    monkeypatch.setattr(lifecycle, "prepare", fake_prepare)

    assert lifecycle.install(layout=current) is current
    assert lifecycle.upgrade(layout=current) is current
    assert calls == [current, current]


def test_gway_manifest_declares_install_layout_and_importable_lifecycle_hooks():
    repository_root = Path(__file__).resolve().parents[3]
    manifest = tomllib.loads((repository_root / "gway.toml").read_text())

    assert manifest["install"] == {
        "root": "/opt/arthexis",
        "checkout": "app",
        "environment": ".venv",
    }

    hooks = manifest["lifecycle"]
    assert hooks == {
        "install": "apps.core.system.lifecycle:install",
        "upgrade": "apps.core.system.lifecycle:upgrade",
    }

    for target in hooks.values():
        module_name, function_name = target.split(":", 1)
        function = getattr(import_module(module_name), function_name)
        assert callable(function)
