from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import CommandError
import pytest

from apps.ftp.management.commands import runftpserver


def test_runftpserver_requires_ftp_extra_when_pyftpdlib_is_missing(monkeypatch):
    real_import_module = runftpserver.import_module

    def fake_import_module(module_name: str):
        if module_name == "apps.ftp.authorizers":
            raise ModuleNotFoundError(
                "No module named 'pyftpdlib'",
                name="pyftpdlib",
            )
        return real_import_module(module_name)

    monkeypatch.setattr(runftpserver, "import_module", fake_import_module)

    with pytest.raises(CommandError, match="pyftpdlib is not installed"):
        call_command("runftpserver", "--dry-run")
