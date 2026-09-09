from pathlib import Path

import manage
from config import loadenv as loadenv_module


def test_gway_service_profile_maps_to_node_role(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(loadenv_module, "BASE_DIR", tmp_path)
    monkeypatch.delenv("NODE_ROLE", raising=False)
    monkeypatch.setenv("GWAY_SERVICE_PROFILE", "Control")

    loadenv_module.loadenv()

    assert loadenv_module.os.environ["NODE_ROLE"] == "Control"


def test_explicit_node_role_beats_gway_service_profile(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(loadenv_module, "BASE_DIR", tmp_path)
    monkeypatch.setenv("NODE_ROLE", "Satellite")
    monkeypatch.setenv("GWAY_SERVICE_PROFILE", "Control")

    loadenv_module.loadenv()

    assert loadenv_module.os.environ["NODE_ROLE"] == "Satellite"


def test_gway_profile_beats_stale_role_lock(monkeypatch, tmp_path: Path) -> None:
    locks = tmp_path / ".locks"
    locks.mkdir()
    (locks / "role.lck").write_text("Terminal\n", encoding="utf-8")
    monkeypatch.setattr(loadenv_module, "BASE_DIR", tmp_path)
    monkeypatch.delenv("NODE_ROLE", raising=False)
    monkeypatch.setenv("GWAY_SERVICE_PROFILE", "Control")

    loadenv_module.loadenv()

    assert manage._is_terminal_node(tmp_path) is False


def test_role_lock_remains_fallback_without_managed_profile(
    monkeypatch, tmp_path: Path
) -> None:
    locks = tmp_path / ".locks"
    locks.mkdir()
    (locks / "role.lck").write_text("Satellite\n", encoding="utf-8")
    monkeypatch.delenv("NODE_ROLE", raising=False)
    monkeypatch.delenv("GWAY_SERVICE_PROFILE", raising=False)

    assert manage._is_terminal_node(tmp_path) is False
