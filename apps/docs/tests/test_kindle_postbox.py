from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.docs import kindle_postbox, node_features
from apps.nodes.models import Node


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.django_db
def test_build_suite_documentation_bundle_collects_docs_library_roots(tmp_path):
    _write(tmp_path / "README.md", "# Root\n")
    _write(tmp_path / "docs" / "operations" / "runbook.md", "# Runbook\n")
    _write(tmp_path / "apps" / "docs" / "cookbooks" / "field.md", "# Field\n")
    _write(tmp_path / "docs" / ".private" / "hidden.md", "# Hidden\n")
    _write(tmp_path / "docs" / "ignored.py", "print('ignored')\n")

    bundle = kindle_postbox.build_suite_documentation_bundle(
        base_dir=tmp_path,
        output_dir=tmp_path / "out",
    )

    output = bundle.output_path.read_text(encoding="utf-8")
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))

    assert bundle.document_count == 3
    assert "README.md" in output
    assert "docs/operations/runbook.md" in output
    assert "apps/docs/cookbooks/field.md" in output
    assert "hidden.md" not in output
    assert manifest["sources"] == list(bundle.sources)


@pytest.mark.django_db
def test_sync_to_explicit_kindle_target_copies_bundle_to_documents_dir(tmp_path):
    _write(tmp_path / "README.md", "# Root\n")
    target = tmp_path / "kindle"
    (target / "documents").mkdir(parents=True)

    result = kindle_postbox.sync_to_kindle_postboxes(
        base_dir=tmp_path,
        output_dir=tmp_path / "out",
        targets=[target],
    )

    copied = target / "documents" / kindle_postbox.KINDLE_POSTBOX_BUNDLE_FILENAME
    assert result.targets[0].status == "copied"
    assert copied.read_text(encoding="utf-8") == result.bundle.output_path.read_text(
        encoding="utf-8"
    )


@pytest.mark.django_db
def test_sync_to_claimed_kindle_paths_uses_usb_inventory(monkeypatch, tmp_path):
    _write(tmp_path / "README.md", "# Root\n")
    target = tmp_path / "kindle"
    (target / "documents").mkdir(parents=True)
    calls: list[tuple[str, bool]] = []

    def _claimed_paths(role: str, *, refresh: bool = False) -> list[str]:
        calls.append((role, refresh))
        return [str(target)]

    monkeypatch.setattr(kindle_postbox.usb_inventory, "claimed_paths", _claimed_paths)

    result = kindle_postbox.sync_to_kindle_postboxes(
        base_dir=tmp_path,
        output_dir=tmp_path / "out",
        refresh_usb=True,
    )

    assert calls == [(kindle_postbox.KINDLE_POSTBOX_USB_CLAIM, True)]
    assert result.targets[0].status == "copied"


@pytest.mark.django_db
def test_sync_to_kindle_postboxes_dry_run_does_not_write_target(tmp_path):
    _write(tmp_path / "README.md", "# Root\n")
    target = tmp_path / "kindle"
    (target / "documents").mkdir(parents=True)

    result = kindle_postbox.sync_to_kindle_postboxes(
        base_dir=tmp_path,
        output_dir=tmp_path / "out",
        targets=[target],
        dry_run=True,
    )

    copied = target / "documents" / kindle_postbox.KINDLE_POSTBOX_BUNDLE_FILENAME
    assert result.targets[0].status == "would-copy"
    assert not copied.exists()


def test_kindle_postbox_node_feature_is_control_only(monkeypatch, tmp_path):
    monkeypatch.setattr(kindle_postbox.usb_inventory, "has_usb_inventory_tools", lambda: True)
    control = SimpleNamespace(role=SimpleNamespace(name="Control"))
    terminal = SimpleNamespace(role=SimpleNamespace(name="Terminal"))

    assert (
        node_features.check_node_feature(
            "kindle-postbox",
            node=control,
            base_dir=tmp_path,
            base_path=tmp_path,
        )
        is True
    )
    assert (
        node_features.check_node_feature(
            "kindle-postbox",
            node=terminal,
            base_dir=tmp_path,
            base_path=tmp_path,
        )
        is False
    )


def test_kindle_postbox_sync_command_rejects_non_control_node(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Node,
        "get_local",
        classmethod(lambda cls: SimpleNamespace(role=SimpleNamespace(name="Terminal"))),
    )

    with pytest.raises(CommandError, match="only available on Control nodes"):
        call_command(
            "docs",
            "kindle-postbox",
            "sync",
            "--output-dir",
            str(tmp_path / "out"),
        )


@pytest.mark.django_db
def test_kindle_postbox_build_command_emits_json(tmp_path):
    _write(tmp_path / "README.md", "# Root\n")
    stdout = StringIO()

    call_command(
        "docs",
        "kindle-postbox",
        "build",
        "--output-dir",
        str(tmp_path / "out"),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["document_count"] >= 1
    assert payload["output_path"].endswith(kindle_postbox.KINDLE_POSTBOX_BUNDLE_FILENAME)
