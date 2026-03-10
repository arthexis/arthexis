from __future__ import annotations

from config import loadenv as loadenv_module


def test_loadenv_includes_user_env_files(tmp_path, monkeypatch) -> None:
    """loadenv should include persisted per-user env files after root-level files."""
    (tmp_path / ".env").write_text("FROM_ROOT=1\n", encoding="utf-8")
    user_env_dir = tmp_path / "var" / "user_env"
    user_env_dir.mkdir(parents=True)
    (user_env_dir / "7.env").write_text("FROM_USER=1\n", encoding="utf-8")

    called_paths: list[str] = []

    def fake_load_dotenv(path, override=False):
        called_paths.append(str(path.relative_to(tmp_path)))

    monkeypatch.setattr(loadenv_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(loadenv_module, "load_dotenv", fake_load_dotenv)

    loadenv_module.loadenv()

    assert called_paths == [".env", "var/user_env/7.env"]
