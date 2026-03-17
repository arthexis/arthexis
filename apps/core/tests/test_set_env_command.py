from __future__ import annotations

import io
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

def _read_env(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def test_set_env_raises_when_missing_key(settings, tmp_path):
    settings.BASE_DIR = tmp_path

    with pytest.raises(CommandError):
        call_command("env", "--get", "MISSING")

