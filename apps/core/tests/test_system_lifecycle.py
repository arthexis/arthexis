from __future__ import annotations

from pathlib import Path

from apps.core.system import lifecycle


def _layout() -> lifecycle.InstallationLayout:
    return lifecycle.InstallationLayout(
        root=Path("/managed"),
        checkout=Path("/managed/app"),
    )


def test_migrate_runs_noninteractive_manage_command(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...], lifecycle.InstallationLayout | None]] = []

    def run_manage(
        command: str,
        *arguments: str,
        layout: lifecycle.InstallationLayout | None = None,
    ) -> None:
        calls.append((command, arguments, layout))

    monkeypatch.setattr(lifecycle, "run_manage", run_manage)

    selected = _layout()
    lifecycle.migrate(layout=selected)

    assert calls == [("migrate", ("--noinput",), selected)]


def test_collectstatic_runs_noninteractive_manage_command(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...], lifecycle.InstallationLayout | None]] = []

    def run_manage(
        command: str,
        *arguments: str,
        layout: lifecycle.InstallationLayout | None = None,
    ) -> None:
        calls.append((command, arguments, layout))

    monkeypatch.setattr(lifecycle, "run_manage", run_manage)

    selected = _layout()
    lifecycle.collectstatic(layout=selected)

    assert calls == [("collectstatic", ("--noinput",), selected)]


def test_ensure_local_node_runs_management_command(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...], lifecycle.InstallationLayout | None]] = []

    def run_manage(
        command: str,
        *arguments: str,
        layout: lifecycle.InstallationLayout | None = None,
    ) -> None:
        calls.append((command, arguments, layout))

    monkeypatch.setattr(lifecycle, "run_manage", run_manage)

    selected = _layout()
    lifecycle.ensure_local_node(layout=selected)

    assert calls == [("ensure_local_node", (), selected)]


def test_prepare_ensures_local_node_after_migrations(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    selected = lifecycle.InstallationLayout(root=tmp_path, checkout=tmp_path / "app")
    selected.checkout.mkdir()

    monkeypatch.setattr(lifecycle, "migrate", lambda **kwargs: calls.append("migrate"))
    monkeypatch.setattr(
        lifecycle,
        "ensure_local_node",
        lambda **kwargs: calls.append("ensure_local_node"),
    )
    monkeypatch.setattr(
        lifecycle, "collectstatic", lambda **kwargs: calls.append("collectstatic")
    )

    assert lifecycle.prepare(layout=selected) is selected
    assert calls == ["migrate", "ensure_local_node", "collectstatic"]


def test_install_and_upgrade_are_application_preparation_hooks(monkeypatch) -> None:
    calls: list[tuple[lifecycle.InstallationLayout | None, str | None]] = []

    def prepare(
        *,
        layout: lifecycle.InstallationLayout | None = None,
        site: str | None = None,
        run_migrations: bool = True,
        run_collectstatic: bool = True,
    ) -> lifecycle.InstallationLayout:
        del run_migrations, run_collectstatic
        calls.append((layout, site))
        assert layout is not None
        return layout

    monkeypatch.setattr(lifecycle, "prepare", prepare)

    selected = _layout()

    assert lifecycle.install(layout=selected) is selected
    assert lifecycle.upgrade(layout=selected) is selected
    assert calls == [(selected, None), (selected, None)]


def test_run_python_uses_interpreter_that_invoked_lifecycle(monkeypatch) -> None:
    calls: list[tuple[list[str], Path, bool, bool, dict[str, str]]] = []

    def run(arguments, *, cwd, check, text, env):
        calls.append((arguments, cwd, check, text, env))
        return object()

    monkeypatch.setattr(lifecycle.subprocess, "run", run)
    monkeypatch.setattr(
        lifecycle, "current_python", lambda: "/managed/.venv/bin/python"
    )

    selected = _layout()
    lifecycle.run_python(["manage.py", "check"], layout=selected)

    assert calls[0][:4] == (
        ["/managed/.venv/bin/python", "manage.py", "check"],
        Path("/managed/app"),
        True,
        True,
    )
    assert calls[0][4]["ARTHEXIS_MODE"] == "installed"


def test_lifecycle_does_not_own_environment_or_package_installation() -> None:
    selected = _layout()

    assert not hasattr(selected, "environment")
    assert not hasattr(selected, "python")
    assert not hasattr(lifecycle, "ensure_environment")
    assert not hasattr(lifecycle, "install_project")
