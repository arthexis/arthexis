from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

def test_set_env_raises_when_missing_key(settings, tmp_path):
    settings.BASE_DIR = tmp_path

    with pytest.raises(CommandError):
        call_command("env", "--get", "MISSING")

