import base64
from pathlib import Path
from types import SimpleNamespace

from scripts import gway_burn


def _public_key(body: bytes = b"gway-test-key") -> str:
    encoded = base64.b64encode(body).decode("ascii")
    return f"ssh-ed25519 {encoded} gway-test"


def test_resolve_parent_public_key_prefers_environment(monkeypatch, tmp_path: Path):
    configured = tmp_path / "operator.pub"
    configured.write_text(_public_key(), encoding="utf-8")
    monkeypatch.setenv(gway_burn.KEY_FILE_ENV, str(configured))

    assert gway_burn.resolve_parent_public_key(home=tmp_path) == configured.resolve()


def test_resolve_parent_public_key_uses_standard_ed25519_key(monkeypatch, tmp_path: Path):
    monkeypatch.delenv(gway_burn.KEY_FILE_ENV, raising=False)
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    key_path = ssh_dir / "id_ed25519.pub"
    key_path.write_text(_public_key(), encoding="utf-8")

    assert gway_burn.resolve_parent_public_key(home=tmp_path) == key_path.resolve()


def test_public_key_fingerprint_is_sha256():
    fingerprint = gway_burn.public_key_fingerprint(_public_key(b"stable-key"))

    assert fingerprint.startswith("SHA256:")
    assert "=" not in fingerprint


def test_main_injects_default_key_and_reports_provenance(monkeypatch, tmp_path, capsys):
    key_path = tmp_path / "parent.pub"
    key_path.write_text(_public_key(), encoding="utf-8")
    monkeypatch.setattr(gway_burn, "resolve_parent_public_key", lambda: key_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gway_burn.subprocess, "run", fake_run)

    assert gway_burn.main(["--reserve-number", "4"]) == 0
    command, kwargs = calls[0]
    assert command[-4:] == [
        "--recovery-authorized-key-file",
        str(key_path),
        "--reserve-number",
        "4",
    ]
    assert kwargs["check"] is False
    output = capsys.readouterr().out
    assert f"recovery_ssh_key_source={key_path}" in output
    assert "recovery_ssh_key_fingerprint=SHA256:" in output


def test_main_preserves_explicit_recovery_choice(monkeypatch):
    calls = []
    monkeypatch.setattr(
        gway_burn,
        "resolve_parent_public_key",
        lambda: (_ for _ in ()).throw(AssertionError("must not auto-resolve")),
    )
    monkeypatch.setattr(
        gway_burn.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0),
    )

    assert gway_burn.main(["--skip-recovery-ssh", "--reserve-number", "4"]) == 0
    assert "--skip-recovery-ssh" in calls[0]
