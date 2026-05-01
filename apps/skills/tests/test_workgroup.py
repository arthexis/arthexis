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
    stdout = StringIO()

    call_command(
        "codex_workgroup", "path", "--codex-home", str(tmp_path), stdout=stdout
    )

    assert stdout.getvalue().strip() == str(workgroup_path(codex_home=tmp_path))
