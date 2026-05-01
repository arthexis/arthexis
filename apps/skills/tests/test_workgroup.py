from __future__ import annotations

from io import StringIO

from django.core.management import call_command

from apps.skills.workgroup import (
    WORKGROUP_FILENAME,
    ensure_workgroup_file,
    read_workgroup_text,
    workgroup_path,
)


def test_workgroup_service_initializes_local_coordination_file(tmp_path):
    path = ensure_workgroup_file(codex_home=tmp_path)

    assert path == tmp_path / WORKGROUP_FILENAME
    assert "Commander Overview" in path.read_text(encoding="utf-8")
    assert read_workgroup_text(codex_home=tmp_path) == path.read_text(encoding="utf-8")


def test_workgroup_command_reports_configured_path(tmp_path):
    expected = workgroup_path(codex_home=tmp_path)
    path_stdout = StringIO()

    call_command(
        "codex_workgroup", "path", "--codex-home", str(tmp_path), stdout=path_stdout
    )

    assert path_stdout.getvalue().strip() == str(expected)

    ensure_stdout = StringIO()
    call_command(
        "codex_workgroup",
        "ensure",
        "--codex-home",
        str(tmp_path),
        stdout=ensure_stdout,
    )

    assert ensure_stdout.getvalue().strip() == str(expected)

    read_stdout = StringIO()
    call_command(
        "codex_workgroup", "read", "--codex-home", str(tmp_path), stdout=read_stdout
    )

    assert "Commander Overview" in read_stdout.getvalue()
