from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def _read_env(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_set_env_sets_and_gets_value(settings, tmp_path):
    settings.BASE_DIR = tmp_path

    call_command("set_env", "--set", "API_KEY", "alpha")

    env_path = tmp_path / "arthexis.env"
    assert env_path.exists()
    assert _read_env(env_path) == "API_KEY=\"alpha\"\n"

    stdout = io.StringIO()
    call_command("set_env", "--get", "API_KEY", stdout=stdout)
    assert stdout.getvalue().strip() == "API_KEY=alpha"


def test_set_env_lists_and_deletes_values(settings, tmp_path):
    settings.BASE_DIR = tmp_path

    call_command("set_env", "--set", "FEATURE_FLAG", "on")
    call_command("set_env", "--set", "SECOND_FLAG", "off")

    stdout = io.StringIO()
    call_command("set_env", "--list", stdout=stdout)
    output = stdout.getvalue().strip().splitlines()
    assert "FEATURE_FLAG=on" in output
    assert "SECOND_FLAG=off" in output

    call_command("set_env", "--delete", "FEATURE_FLAG")
    env_path = tmp_path / "arthexis.env"
    assert _read_env(env_path) == "SECOND_FLAG=\"off\"\n"

    call_command("set_env", "--delete", "SECOND_FLAG")
    assert not env_path.exists()


def test_set_env_raises_when_missing_key(settings, tmp_path):
    settings.BASE_DIR = tmp_path

    with pytest.raises(CommandError):
        call_command("set_env", "--get", "MISSING")


@pytest.mark.parametrize(
    ("key", "value", "expected_line"),
    [
        ("KEY1", "simple", "KEY1=\"simple\""),
        ("KEY2", "with spaces", "KEY2=\"with spaces\""),
        ("KEY3", "", "KEY3=\"\""),
        ("KEY4", 'with "quotes"', "KEY4=\"with \\\"quotes\\\"\""),
        ("KEY5", "contains ' quote", "KEY5=\"contains ' quote\""),
        ("KEY6", "value with $HOME", "KEY6=\"value with \\$HOME\""),
        ("KEY7", "value with `cmd`", "KEY7=\"value with \\`cmd\\`\""),
    ],
)
def test_set_env_formats_values(settings, tmp_path, key, value, expected_line):
    settings.BASE_DIR = tmp_path

    call_command("set_env", "--set", key, value)

    env_path = tmp_path / "arthexis.env"
    assert expected_line in _read_env(env_path)

    stdout = io.StringIO()
    call_command("set_env", "--get", key, stdout=stdout)
    assert stdout.getvalue().strip() == f"{key}={value}"


def test_set_env_rejects_invalid_key(settings, tmp_path):
    settings.BASE_DIR = tmp_path

    with pytest.raises(CommandError):
        call_command("set_env", "--set", "FEATURE-FLAG", "on")
