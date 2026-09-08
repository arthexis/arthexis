from __future__ import annotations

from pathlib import Path

from apps.core.system import lifecycle


def test_migrate_runs_noninteractive_manage_command(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...], lifecycle.InstallationLayout | None]] = []

    def run_manage(
        command: str,
        *arguments: str,
        layout: lifecycle.InstallationLayout | None = None,
    ) -> None:
        calls.append((command, arguments, layout))

    monkeypatch.setattr(lifecycle, "run_manage", run_manage)

    selected = lifecycle.InstallationLayout(
        root=Path("/managed"),
        checkout=Path("/managed/app"),
        environment=Path("/managed/.venv"),
    )
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

    selected = lifecycle.InstallationLayout(
        root=Path("/managed"),
        checkout=Path("/managed/app"),
        environment=Path("/managed/.venv"),
    )
    lifecycle.collectstatic(layout=selected)

    assert calls == [("collectstatic", ("--noinput",), selected)]


def test_install_and_upgrade_are_application_preparation_hooks(monkeypatch) -> None:
    calls: list[lifecycle.InstallationLayout | None] = []

    def prepare(
        *,
        layout: lifecycle.InstallationLayout | None = None,
        run_migrations: bool = True,
        run_collectstatic: bool = True,
    ) -> lifecycle.InstallationLayout:
        del run_migrations, run_collectstatic
        calls.append(layout)
        assert layout is not None
        return layout

    monkeypatch.setattr(lifecycle, "prepare", prepare)

    selected = lifecycle.InstallationLayout(
        root=Path("/managed"),
        checkout=Path("/managed/app"),
        environment=Path("/managed/.venv"),
    )

    assert lifecycle.install(layout=selected) is selected
    assert lifecycle.upgrade(layout=selected) is selected
    assert calls == [selected, selected]


def test_run_python_uses_interpreter_that_invoked_lifecycle(monkeypatch) -> None:
    calls: list[tuple[list[str], Path, bool, bool]] = []

    def run(arguments, *, cwd, check, text):
        calls.append((arguments, cwd, check, text))
        return object()

    monkeypatch.setattr(lifecycle.subprocess, "run", run)
    monkeypatch.setattr(lifecycle, "current_python", lambda: "/managed/.venv/bin/python")

    selected = lifecycle.InstallationLayout(
        root=Path("/managed"),
        checkout=Path("/managed/app"),
        environment=Path("/managed/.venv"),
    )
    lifecycle.run_python(["manage.py", "check"], layout=selected)

    assert calls == [
        (
            ["/managed/.venv/bin/python", "manage.py", "check"],
            Path("/managed/app"),
            True,
            True,
        )
    ]


def test_lifecycle_does_not_own_environment_or_package_installation() -> None:
    assert not hasattr(lifecycle, "ensure_environment")
    assert not hasattr(lifecycle, "install_project")
