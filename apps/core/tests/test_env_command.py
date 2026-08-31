from __future__ import annotations

import stat
from collections import OrderedDict

from apps.core.management.commands.env import write_env


def test_write_env_uses_owner_only_permissions(tmp_path) -> None:
    """Operator environment values must never inherit broad file permissions."""

    path = tmp_path / "arthexis.env"

    write_env(path, OrderedDict([("API_TOKEN", "secret-value")]))

    assert path.read_text(encoding="utf-8") == 'API_TOKEN="secret-value"\n'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
