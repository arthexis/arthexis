from pathlib import Path

import pytest

from apps.core.system import lifecycle


def test_managed_layout_defaults_to_opt(monkeypatch):
    monkeypatch.delenv("ARTHEXIS_MANAGED_ROOT", raising=False)

    layout = lifecycle.managed_layout()

    assert layout.root == Path("/opt/arthexis")
    assert layout.checkout == Path("/opt/arthexis/app")
    assert layout.environment == Path("/opt/arthexis/.venv")


def test_managed_layout_accepts_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTHEXIS_MANAGED_ROOT", str(tmp_path))

    layout = lifecycle.managed_layout()

    assert layout.root == tmp_path
    assert layout.checkout == tmp_path / "app"
    assert layout.environment == tmp_path / ".venv"


def test_prepare_managed_install_runs_canonical_steps(monkeypatch, tmp_path):
    layout = lifecycle.ManagedLayout(
        root=tmp_path,
        checkout=tmp_path / "app",
        environment=tmp_path / ".venv",
    )
    layout.checkout.mkdir()
    calls = []

    monkeypatch.setattr(lifecycle, "ensure_environment", lambda current: calls.append("venv"))
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

    result = lifecycle.prepare_managed_install(layout=layout)

    assert result == layout
    assert calls == ["venv", ("install", False), "migrate", "collectstatic"]


def test_prepare_managed_install_requires_checkout(tmp_path):
    layout = lifecycle.ManagedLayout(
        root=tmp_path,
        checkout=tmp_path / "missing",
        environment=tmp_path / ".venv",
    )

    with pytest.raises(FileNotFoundError):
        lifecycle.prepare_managed_install(layout=layout)
