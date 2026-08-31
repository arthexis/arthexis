from __future__ import annotations

from pathlib import Path

from apps.nodes.services import path_safety


def test_resolve_within_rejects_symlink_loop(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to(second, target_is_directory=True)
    second.symlink_to(first, target_is_directory=True)

    assert path_safety._resolve_within(tmp_path, first / "socket.sock") is None


def test_resolve_within_preserves_relative_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    root = Path("node") / "ipc"
    candidate = root / "peer.sock"

    assert path_safety._resolve_within(root, candidate) == (
        tmp_path / "node" / "ipc" / "peer.sock"
    )
