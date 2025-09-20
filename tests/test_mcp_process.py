from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from core.mcp import process


class PidMatchesServerTests(SimpleTestCase):
    def test_returns_true_when_cmdline_matches_expected_server(self) -> None:
        manage_path = str(process._BASE_DIR / "manage.py")
        cmdline = "\0".join(["/usr/bin/python3", manage_path, "mcp_sigil_server"]) + "\0"

        with mock.patch("core.mcp.process._pid_running", return_value=True), \
            mock.patch("core.mcp.process.sys.platform", "linux"), \
            mock.patch("core.mcp.process.Path.read_bytes", return_value=cmdline.encode("utf-8")):
            self.assertTrue(process._pid_matches_server(1234))

    def test_returns_false_when_cmdline_does_not_match(self) -> None:
        manage_path = str(process._BASE_DIR / "manage.py")
        cmdline = "\0".join(["/usr/bin/python3", manage_path, "not_the_server"]) + "\0"

        with mock.patch("core.mcp.process._pid_running", return_value=True), \
            mock.patch("core.mcp.process.sys.platform", "linux"), \
            mock.patch("core.mcp.process.Path.read_bytes", return_value=cmdline.encode("utf-8")):
            self.assertFalse(process._pid_matches_server(4321))


class StopServerTests(SimpleTestCase):
    def test_stop_server_ignores_unrelated_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pid_file = Path(tmp_dir) / "mcp.pid"
            pid_file.write_text("999")

            with mock.patch("core.mcp.process._PID_FILE", pid_file), \
                mock.patch("core.mcp.process._pid_matches_server", return_value=False):
                with self.assertRaises(process.ServerNotRunningError):
                    process.stop_server()

            self.assertFalse(pid_file.exists())
