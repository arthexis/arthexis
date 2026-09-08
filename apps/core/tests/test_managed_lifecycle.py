from importlib import import_module
from pathlib import Path
import tomllib

import pytest

from apps.core.system import lifecycle


def test_layout_defaults_to_opt(monkeypatch):
    monkeypatch.delenv("ARTHEXIS_MANAGED_ROOT", raising=False)

    current = lifecycle.layout()

    assert current.root == Path("/opt/arthexis")
    assert current.checkout == Path("/opt/arthexis/app")
    assert current.environment == Path("/opt/arthexis/.venv")


def test_layout_accepts_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTHEXIS_MANAGED_ROOT", str(tmp_path))

    current = lifecycle.layout()

    assert current.root == tmp_path
    assert current.checkout == tmp_path / "app"
    assert current.environment == tmp_path / ".venv"


def test_prepare_runs_canonical_steps(monkeypatch, tmp_path):
    current = lifecycle.InstallationLayout(
        root=tmp_path,
        checkout=tmp_path / "app",
        environment=tmp_path / ".venv",
    )
    current.checkout.mkdir()
    calls = []

    monkeypatch.setattr(lifecycle, "ensure_environment", lambda value: calls.append("venv"))
    monkeypatch.setattr(
        lifecycle,
        "install_project",
        lambda *, layout, editable: calls.append(("install", editable)),
    )
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
    assert calls == ["venv", ("install", False), "migrate", "collectstatic"]


def test_prepare_requires_checkout(tmp_path):
    current = lifecycle.InstallationLayout(
        root=tmp_path,
        checkout=tmp_path / "missing",
        environment=tmp_path / ".venv",
    )

    with pytest.raises(FileNotFoundError):
        lifecycle.prepare(layout=current)


def test_install_and_upgrade_are_semantic_prepare_hooks(monkeypatch, tmp_path):
    current = lifecycle.InstallationLayout(
        root=tmp_path,
        checkout=tmp_path / "app",
        environment=tmp_path / ".venv",
    )
    calls = []

    def fake_prepare(*, layout, editable=False, **_kwargs):
        calls.append((layout, editable))
        return layout

    monkeypatch.setattr(lifecycle, "prepare", fake_prepare)

    assert lifecycle.install(layout=current) is current
    assert lifecycle.upgrade(layout=current, editable=True) is current
    assert calls == [(current, False), (current, True)]


def test_gway_manifest_lifecycle_hooks_are_importable():
    repository_root = Path(__file__).resolve().parents[3]
    manifest = tomllib.loads((repository_root / "gway.toml").read_text())

    hooks = manifest["lifecycle"]
    assert hooks == {
        "install": "apps.core.system.lifecycle:install",
        "upgrade": "apps.core.system.lifecycle:upgrade",
    }

    for target in hooks.values():
        module_name, function_name = target.split(":", 1)
        function = getattr(import_module(module_name), function_name)
        assert callable(function)


def test_managed_names_remain_compatibility_aliases(tmp_path):
    current = lifecycle.managed_layout(tmp_path)

    assert isinstance(current, lifecycle.InstallationLayout)
    assert lifecycle.ManagedLayout is lifecycle.InstallationLayout
    assert lifecycle.DEFAULT_MANAGED_ROOT == lifecycle.DEFAULT_INSTALL_ROOT
