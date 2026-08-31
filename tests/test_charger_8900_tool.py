from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "charger_8900_http.sh"
SCRIPT_ARGUMENT = "scripts/charger_8900_http.sh"


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"

    configured = os.environ.get("GIT_BASH")
    if configured and Path(configured).is_file():
        return configured

    for base in (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ):
        if not base:
            continue
        candidate = Path(base) / "Git" / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)

    return "bash"


BASH = _bash_executable()


def _to_bash_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return resolved.as_posix()

    drive = resolved.drive.rstrip(":").lower()
    if not drive:
        return resolved.as_posix()
    return f"/{drive}{resolved.as_posix()[len(resolved.drive):]}"


def _from_bash_path(value: str) -> Path:
    if os.name == "nt" and len(value) > 3 and value[0] == "/" and value[2] == "/":
        return Path(f"{value[1]}:{value[2:]}")
    return Path(value)


def _prepend_bash_path(env: dict[str, str], path: Path) -> None:
    if os.name == "nt":
        env["PATH"] = f"{path.resolve()}{os.pathsep}{env['PATH']}"
    else:
        env["PATH"] = f"{_to_bash_path(path)}:{env['PATH']}"


def test_default_env_path_stays_out_of_root_env_glob() -> None:
    env = os.environ.copy()
    env.pop("CHARGER_8900_ENV_FILE", None)

    result = subprocess.run(
        [BASH, SCRIPT_ARGUMENT, "env-path"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    env_path = _from_bash_path(result.stdout.strip())
    assert env_path == ROOT / ".private" / "charger-8900.env"
    assert env_path.parent != ROOT


def test_default_template_path_stays_out_of_repo_root(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["CHARGER_8900_ENV_FILE"] = _to_bash_path(tmp_path / "charger-8900.env")
    env.pop("CHARGER_8900_TEMPLATE_FILE", None)

    result = subprocess.run(
        [BASH, SCRIPT_ARGUMENT, "check-env"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    expected_template = ROOT / "config" / "templates" / "charger-8900.env.template"
    assert result.returncode == 0, result.stderr
    assert f"Template file: {_to_bash_path(expected_template)}" in result.stdout
    assert expected_template.parent != ROOT


def run_tool(
    env_file: Path, template_file: Path
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CHARGER_8900_ENV_FILE"] = _to_bash_path(env_file)
    env["CHARGER_8900_TEMPLATE_FILE"] = _to_bash_path(template_file)
    return subprocess.run(
        [BASH, SCRIPT_ARGUMENT, "init-env"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_init_env_recreates_missing_private_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "charger-8900.env"
    template_file = tmp_path / "charger-8900.env.template"
    template_file.write_text(
        "\n".join(
            [
                "CHARGER_8900_HOST=192.168.129.191",
                "CHARGER_8900_PORT=8900",
                "CHARGER_8900_INTERFACE=eth0",
                "CHARGER_8900_USER=",
                "CHARGER_8900_PASSWORD=",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = run_tool(env_file, template_file)

    assert result.returncode == 0, result.stderr
    assert env_file.exists()
    if os.name != "nt":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "CHARGER_8900_HOST=192.168.129.191" in env_file.read_text(encoding="utf-8")
    assert "Username set: no" in result.stdout
    assert "Password set: no" in result.stdout


def test_init_env_appends_template_keys_without_overwriting_secrets(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "charger-8900.env"
    template_file = tmp_path / "charger-8900.env.template"
    env_file.write_text(
        "\n".join(
            [
                "CHARGER_8900_HOST=10.0.0.50",
                "CHARGER_8900_USER=admin",
                "CHARGER_8900_PASSWORD=keep-this-secret",
                "",
            ]
        ),
        encoding="utf-8",
    )
    template_file.write_text(
        "\n".join(
            [
                "CHARGER_8900_HOST=192.168.129.191",
                "CHARGER_8900_PORT=8900",
                "CHARGER_8900_INTERFACE=eth0",
                "CHARGER_8900_USER=",
                "CHARGER_8900_PASSWORD=",
                "CHARGER_8900_MAX_TIME=2",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = run_tool(env_file, template_file)

    assert result.returncode == 0, result.stderr
    env_text = env_file.read_text(encoding="utf-8")
    assert "CHARGER_8900_HOST=10.0.0.50" in env_text
    assert "CHARGER_8900_USER=admin" in env_text
    assert "CHARGER_8900_PASSWORD=keep-this-secret" in env_text
    assert "CHARGER_8900_PORT=8900" in env_text
    assert "CHARGER_8900_INTERFACE=eth0" in env_text
    assert "CHARGER_8900_MAX_TIME=2" in env_text
    assert "Username set: yes" in result.stdout
    assert "Password set: yes" in result.stdout


def test_existing_env_file_permission_hardening_fails_closed(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("requires POSIX command shadowing")

    env_file = tmp_path / "charger-8900.env"
    template_file = tmp_path / "charger-8900.env.template"
    env_file.write_text("CHARGER_8900_HOST=10.0.0.50\n", encoding="utf-8")
    template_file.write_text(env_file.read_text(encoding="utf-8"), encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    chmod = bin_dir / "chmod"
    chmod.write_text("#!/usr/bin/env bash\nexit 42\n", encoding="utf-8")
    chmod.chmod(0o755)

    env = os.environ.copy()
    _prepend_bash_path(env, bin_dir)
    env["CHARGER_8900_ENV_FILE"] = _to_bash_path(env_file)
    env["CHARGER_8900_TEMPLATE_FILE"] = _to_bash_path(template_file)

    result = subprocess.run(
        [BASH, SCRIPT_ARGUMENT, "check-env"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert f"failed to enforce 0600 on env file: {_to_bash_path(env_file)}" in result.stderr


def test_env_loader_ignores_non_charger_keys(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("requires POSIX command shadowing")

    env_file = tmp_path / "charger-8900.env"
    template_file = tmp_path / "charger-8900.env.template"
    env_file.write_text(
        "\n".join(
            [
                "PATH=/definitely/not/the/test/bin",
                "TMPDIR=/definitely/not/tmp",
                "CHARGER_8900_HOST=10.0.0.50",
                "CHARGER_8900_PORT=8900",
                "",
            ]
        ),
        encoding="utf-8",
    )
    template_file.write_text(env_file.read_text(encoding="utf-8"), encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\nprintf 'HTTP/1.0 200 OK\\r\\n\\r\\n'\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    env = os.environ.copy()
    _prepend_bash_path(env, bin_dir)
    env["CHARGER_8900_ENV_FILE"] = _to_bash_path(env_file)
    env["CHARGER_8900_TEMPLATE_FILE"] = _to_bash_path(template_file)

    result = subprocess.run(
        [BASH, SCRIPT_ARGUMENT, "headers"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "HTTP/1.0 200 OK" in result.stdout


def test_check_env_rejects_non_numeric_port(tmp_path: Path) -> None:
    env_file = tmp_path / "charger-8900.env"
    template_file = tmp_path / "charger-8900.env.template"
    env_file.write_text(
        "\n".join(
            [
                "CHARGER_8900_HOST=10.0.0.50",
                "CHARGER_8900_PORT=8900-9000",
                "",
            ]
        ),
        encoding="utf-8",
    )
    template_file.write_text(env_file.read_text(encoding="utf-8"), encoding="utf-8")

    result = run_tool(env_file, template_file)

    assert result.returncode != 0
    assert "CHARGER_8900_PORT must be numeric" in result.stderr


def test_check_env_rejects_out_of_range_port(tmp_path: Path) -> None:
    env_file = tmp_path / "charger-8900.env"
    template_file = tmp_path / "charger-8900.env.template"
    env_file.write_text(
        "\n".join(
            [
                "CHARGER_8900_HOST=10.0.0.50",
                "CHARGER_8900_PORT=70000",
                "",
            ]
        ),
        encoding="utf-8",
    )
    template_file.write_text(env_file.read_text(encoding="utf-8"), encoding="utf-8")

    result = run_tool(env_file, template_file)

    assert result.returncode != 0
    assert "CHARGER_8900_PORT must be 1..65535" in result.stderr


def test_auth_headers_removes_temporary_curl_config(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("requires POSIX command shadowing")

    env_file = tmp_path / "charger-8900.env"
    template_file = tmp_path / "charger-8900.env.template"
    env_file.write_text(
        "\n".join(
            [
                "CHARGER_8900_HOST=10.0.0.50",
                "CHARGER_8900_PORT=8900",
                "CHARGER_8900_USER=admin",
                "CHARGER_8900_PASSWORD=keep-this-secret",
                "",
            ]
        ),
        encoding="utf-8",
    )
    template_file.write_text(env_file.read_text(encoding="utf-8"), encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_args_file = tmp_path / "curl-args.txt"
    auth_path_file = tmp_path / "auth-path.txt"
    curl = bin_dir / "curl"
    curl.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "%s\\n" "$@" > "$CURL_ARGS_FILE"',
                "while [[ $# -gt 0 ]]; do",
                '  if [[ "$1" == "--config" ]]; then',
                '    printf "%s\\n" "$2" > "$AUTH_PATH_FILE"',
                '    [[ -f "$2" ]] || exit 31',
                "  fi",
                "  shift",
                "done",
                'printf "HTTP/1.0 200 OK\\r\\n\\r\\n"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    curl.chmod(0o755)

    env = os.environ.copy()
    _prepend_bash_path(env, bin_dir)
    env["TMPDIR"] = _to_bash_path(tmp_path)
    env["CURL_ARGS_FILE"] = _to_bash_path(curl_args_file)
    env["AUTH_PATH_FILE"] = _to_bash_path(auth_path_file)
    env["CHARGER_8900_ENV_FILE"] = _to_bash_path(env_file)
    env["CHARGER_8900_TEMPLATE_FILE"] = _to_bash_path(template_file)

    result = subprocess.run(
        [BASH, SCRIPT_ARGUMENT, "auth-headers"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    auth_path = _from_bash_path(auth_path_file.read_text(encoding="utf-8").strip())
    assert "--config" in curl_args_file.read_text(encoding="utf-8")
    assert not auth_path.exists()
