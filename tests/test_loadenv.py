from pathlib import Path

from config import loadenv as loadenv_module


def test_load_dpapi_env_secret_hydrates_target(monkeypatch, tmp_path: Path):
    credential_path = tmp_path / "credential.dpapi"
    credential_path.write_text("encrypted", encoding="utf-8")

    monkeypatch.setattr(loadenv_module.os, "name", "nt")
    monkeypatch.setenv(
        "ARTHEXIS_DPAPI_ENV_TECNOLOGIA_MAIL_PASSWORD", str(credential_path)
    )
    monkeypatch.delenv("TECNOLOGIA_MAIL_PASSWORD", raising=False)
    monkeypatch.setattr(
        loadenv_module, "_read_windows_dpapi_secret", lambda path: f"secret:{path}"
    )

    loadenv_module._load_dpapi_env_secrets()

    assert loadenv_module.os.environ["TECNOLOGIA_MAIL_PASSWORD"] == (
        f"secret:{credential_path}"
    )


def test_load_dpapi_env_secret_preserves_existing_target(monkeypatch, tmp_path: Path):
    credential_path = tmp_path / "credential.dpapi"
    credential_path.write_text("encrypted", encoding="utf-8")

    monkeypatch.setattr(loadenv_module.os, "name", "nt")
    monkeypatch.setenv(
        "ARTHEXIS_DPAPI_ENV_TECNOLOGIA_MAIL_PASSWORD", str(credential_path)
    )
    monkeypatch.setenv("TECNOLOGIA_MAIL_PASSWORD", "already-set")
    monkeypatch.setattr(
        loadenv_module, "_read_windows_dpapi_secret", lambda path: "new-secret"
    )

    loadenv_module._load_dpapi_env_secrets()

    assert loadenv_module.os.environ["TECNOLOGIA_MAIL_PASSWORD"] == "already-set"
