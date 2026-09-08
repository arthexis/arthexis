from pathlib import Path

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


def test_managed_names_remain_compatibility_aliases(tmp_path):
    current = lifecycle.managed_layout(tmp_path)

    assert isinstance(current, lifecycle.InstallationLayout)
    assert lifecycle.ManagedLayout is lifecycle.InstallationLayout
    assert lifecycle.DEFAULT_MANAGED_ROOT == lifecycle.DEFAULT_INSTALL_ROOT
