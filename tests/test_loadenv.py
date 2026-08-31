import subprocess
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

    calls = []

    def _read_secrets(paths_by_target):
        calls.append(paths_by_target)
        return ({"TECNOLOGIA_MAIL_PASSWORD": f"secret:{credential_path}"}, {})

    monkeypatch.setattr(loadenv_module, "_read_windows_dpapi_secrets", _read_secrets)

    loadenv_module._load_dpapi_env_secrets()

    assert loadenv_module.os.environ["TECNOLOGIA_MAIL_PASSWORD"] == (
        f"secret:{credential_path}"
    )
    assert calls == [{"TECNOLOGIA_MAIL_PASSWORD": str(credential_path)}]


def test_load_dpapi_env_secret_preserves_existing_target(monkeypatch, tmp_path: Path):
    credential_path = tmp_path / "credential.dpapi"
    credential_path.write_text("encrypted", encoding="utf-8")

    monkeypatch.setattr(loadenv_module.os, "name", "nt")
    monkeypatch.setenv(
        "ARTHEXIS_DPAPI_ENV_TECNOLOGIA_MAIL_PASSWORD", str(credential_path)
    )
    monkeypatch.setenv("TECNOLOGIA_MAIL_PASSWORD", "already-set")
    calls = []

    def _read_secrets(paths_by_target):
        calls.append(paths_by_target)
        return ({"TECNOLOGIA_MAIL_PASSWORD": "new-secret"}, {})

    monkeypatch.setattr(
        loadenv_module,
        "_read_windows_dpapi_secrets",
        _read_secrets,
    )

    loadenv_module._load_dpapi_env_secrets()

    assert loadenv_module.os.environ["TECNOLOGIA_MAIL_PASSWORD"] == "already-set"
    assert calls == []


def test_load_dpapi_env_secret_preserves_empty_existing_target(
    monkeypatch,
    tmp_path: Path,
):
    credential_path = tmp_path / "credential.dpapi"
    credential_path.write_text("encrypted", encoding="utf-8")

    monkeypatch.setattr(loadenv_module.os, "name", "nt")
    monkeypatch.setenv(
        "ARTHEXIS_DPAPI_ENV_TECNOLOGIA_MAIL_PASSWORD", str(credential_path)
    )
    monkeypatch.setenv("TECNOLOGIA_MAIL_PASSWORD", "")
    calls = []

    def _read_secrets(paths_by_target):
        calls.append(paths_by_target)
        return ({"TECNOLOGIA_MAIL_PASSWORD": "new-secret"}, {})

    monkeypatch.setattr(
        loadenv_module,
        "_read_windows_dpapi_secrets",
        _read_secrets,
    )

    loadenv_module._load_dpapi_env_secrets()

    assert loadenv_module.os.environ["TECNOLOGIA_MAIL_PASSWORD"] == ""
    assert calls == []


def test_load_dpapi_env_secret_batches_pending_targets(monkeypatch, tmp_path: Path):
    first_path = tmp_path / "first.dpapi"
    second_path = tmp_path / "second.dpapi"
    first_path.write_text("encrypted", encoding="utf-8")
    second_path.write_text("encrypted", encoding="utf-8")

    monkeypatch.setattr(loadenv_module.os, "name", "nt")
    monkeypatch.setenv("ARTHEXIS_DPAPI_ENV_FIRST_SECRET", str(first_path))
    monkeypatch.setenv("ARTHEXIS_DPAPI_ENV_SECOND_SECRET", str(second_path))
    monkeypatch.delenv("FIRST_SECRET", raising=False)
    monkeypatch.delenv("SECOND_SECRET", raising=False)
    calls = []

    def _read_secrets(paths_by_target):
        calls.append(paths_by_target)
        return (
            {
                "FIRST_SECRET": "first-secret",
                "SECOND_SECRET": "second-secret",
            },
            {},
        )

    monkeypatch.setattr(loadenv_module, "_read_windows_dpapi_secrets", _read_secrets)

    loadenv_module._load_dpapi_env_secrets()

    assert calls == [
        {
            "FIRST_SECRET": str(first_path),
            "SECOND_SECRET": str(second_path),
        }
    ]
    assert loadenv_module.os.environ["FIRST_SECRET"] == "first-secret"
    assert loadenv_module.os.environ["SECOND_SECRET"] == "second-secret"


def test_called_process_error_detail_prefers_stderr():
    error = subprocess.CalledProcessError(
        1,
        ["powershell"],
        stderr="invalid dpapi payload\n",
    )

    assert loadenv_module._called_process_error_detail(error) == "invalid dpapi payload"
