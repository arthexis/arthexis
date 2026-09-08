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
    calls: list[tuple[lifecycle.InstallationLayout | None, bool]] = []

    def prepare(
        *,
        layout: lifecycle.InstallationLayout | None = None,
        editable: bool = False,
        run_migrations: bool = True,
        run_collectstatic: bool = True,
    ) -> lifecycle.InstallationLayout:
        del run_migrations, run_collectstatic
        calls.append((layout, editable))
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
    assert calls == [(selected, False), (selected, False)]
