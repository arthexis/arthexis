from __future__ import annotations

import json
import shutil
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
def test_build_suite_documentation_bundle_excludes_existing_postbox_outputs(tmp_path):
    _write(tmp_path / "README.md", "# Root\n")
    output_dir = tmp_path / "docs" / "postbox"
    _write(
        output_dir / kindle_postbox.KINDLE_POSTBOX_BUNDLE_FILENAME,
        "old recursive payload\n",
    )
    _write(
        output_dir / kindle_postbox.KINDLE_POSTBOX_MANIFEST_FILENAME,
        '{"old": true}\n',
    )

    bundle = kindle_postbox.build_suite_documentation_bundle(
        base_dir=tmp_path,
        output_dir=output_dir,
    )

    output = bundle.output_path.read_text(encoding="utf-8")
    assert "old recursive payload" not in output
    assert all("docs/postbox" not in source for source in bundle.sources)


@pytest.mark.django_db
def test_build_suite_documentation_bundle_allows_docs_root_output_dir(tmp_path):
    _write(tmp_path / "README.md", "# Root\n")
    _write(tmp_path / "docs" / "guide.md", "# Guide\n")

    bundle = kindle_postbox.build_suite_documentation_bundle(
        base_dir=tmp_path,
        output_dir=tmp_path / "docs",
    )

    assert "docs/guide.md" in bundle.sources


@pytest.mark.django_db
def test_iter_suite_documentation_files_skips_symlinks_outside_base_dir(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside.md"
    _write(root / "README.md", "# Root\n")
    _write(outside, "# Outside\n")
    link = root / "docs" / "outside.md"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable on this platform: {exc}")

    documents = kindle_postbox.iter_suite_documentation_files(base_dir=root)

    assert outside.resolve() not in documents


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
def test_sync_to_explicit_kindle_target_uses_metadata_preserving_copy(
    monkeypatch,
    tmp_path,
):
    _write(tmp_path / "README.md", "# Root\n")
    target = tmp_path / "kindle"
    (target / "documents").mkdir(parents=True)
    real_copy2 = shutil.copy2
    calls: list[tuple[Path, Path]] = []

    def _copy2(source: Path, destination: Path) -> Path:
        calls.append((Path(source), Path(destination)))
        return Path(real_copy2(source, destination))

    monkeypatch.setattr(kindle_postbox.shutil, "copy2", _copy2)

    result = kindle_postbox.sync_to_kindle_postboxes(
        base_dir=tmp_path,
        output_dir=tmp_path / "out",
        targets=[target],
    )

    assert result.targets[0].status == "copied"
    assert calls == [
        (
            result.bundle.output_path,
            target
            / "documents"
            / f".{kindle_postbox.KINDLE_POSTBOX_BUNDLE_FILENAME}.tmp",
        )
    ]


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
def test_sync_to_explicit_empty_targets_skips_usb_inventory(monkeypatch, tmp_path):
    _write(tmp_path / "README.md", "# Root\n")

    def _claimed_paths(role: str, *, refresh: bool = False) -> list[str]:
        raise AssertionError("USB inventory should not be used for explicit targets=[]")

    monkeypatch.setattr(kindle_postbox.usb_inventory, "claimed_paths", _claimed_paths)

    result = kindle_postbox.sync_to_kindle_postboxes(
        base_dir=tmp_path,
        output_dir=tmp_path / "out",
        targets=[],
    )

    assert result.targets == ()


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


def test_kindle_postbox_available_does_not_require_usb_tools(monkeypatch):
    monkeypatch.setattr(kindle_postbox.usb_inventory, "has_usb_inventory_tools", lambda: False)
    control = SimpleNamespace(role=SimpleNamespace(name="Control"))

    assert kindle_postbox.kindle_postbox_available(node=control) is True


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


def test_kindle_postbox_sync_command_requires_usb_tools_without_target(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        Node,
        "get_local",
        classmethod(lambda cls: SimpleNamespace(role=SimpleNamespace(name="Control"))),
    )
    monkeypatch.setattr(
        kindle_postbox.usb_inventory,
        "has_usb_inventory_tools",
        lambda: False,
    )

    def _claimed_paths(role: str, *, refresh: bool = False) -> list[str]:
        raise AssertionError("USB inventory should be preflighted before discovery")

    monkeypatch.setattr(kindle_postbox.usb_inventory, "claimed_paths", _claimed_paths)

    with pytest.raises(CommandError, match="requires lsblk and findmnt"):
        call_command(
            "docs",
            "kindle-postbox",
            "sync",
            "--output-dir",
            str(tmp_path / "out"),
        )


def test_kindle_postbox_sync_command_fails_when_target_copy_fails(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        Node,
        "get_local",
        classmethod(lambda cls: SimpleNamespace(role=SimpleNamespace(name="Control"))),
    )
    bundle = kindle_postbox.DocumentationBundle(
        output_path=tmp_path / kindle_postbox.KINDLE_POSTBOX_BUNDLE_FILENAME,
        manifest_path=tmp_path / kindle_postbox.KINDLE_POSTBOX_MANIFEST_FILENAME,
        generated_at="2026-05-17T00:00:00+00:00",
        document_count=1,
        byte_count=12,
        sources=("README.md",),
    )
    target_result = kindle_postbox.KindlePostboxTargetResult(
        root_path=tmp_path / "kindle",
        documents_path=tmp_path / "kindle" / "documents",
        output_path=tmp_path
        / "kindle"
        / "documents"
        / kindle_postbox.KINDLE_POSTBOX_BUNDLE_FILENAME,
        status="failed",
        error="permission denied",
    )

    def _sync_to_kindle_postboxes(**kwargs):
        return kindle_postbox.KindlePostboxSyncResult(
            bundle=bundle,
            targets=(target_result,),
            dry_run=False,
        )

    monkeypatch.setattr(
        kindle_postbox,
        "sync_to_kindle_postboxes",
        _sync_to_kindle_postboxes,
    )
    stdout = StringIO()
    stderr = StringIO()

    with pytest.raises(CommandError, match="failed for 1 target"):
        call_command(
            "docs",
            "kindle-postbox",
            "sync",
            "--target",
            str(tmp_path / "kindle"),
            stdout=stdout,
            stderr=stderr,
        )

    assert "failed:" in stdout.getvalue()
    assert "permission denied" in stderr.getvalue()

    json_stdout = StringIO()
    with pytest.raises(CommandError, match="failed for 1 target"):
        call_command(
            "docs",
            "kindle-postbox",
            "sync",
            "--target",
            str(tmp_path / "kindle"),
            "--json",
            stdout=json_stdout,
        )

    payload = json.loads(json_stdout.getvalue())
    assert payload["targets"][0]["status"] == "failed"


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
