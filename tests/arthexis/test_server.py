from unittest.mock import patch

from django.test import SimpleTestCase

from arthexis.server import daphne_command, main


class ServerEntrypointTests(SimpleTestCase):
    def test_daphne_command_binds_locally_by_default(self) -> None:
        command = daphne_command()

        self.assertEqual(command[-5:], ("-b", "127.0.0.1", "-p", "8888", "arthexis.asgi:application"))

    @patch("arthexis.server.os.execv")
    @patch("arthexis.server.daphne_command")
    def test_main_sets_persistent_data_dir_before_exec(self, command, execv) -> None:
        command.return_value = ("/runtime/bin/daphne", "daphne-arg")

        with patch.dict("arthexis.server.os.environ", {}, clear=True):
            main(
                data_dir="/var/lib/arthexis",
                allowed_hosts="0.0.0.0,arthexis.com",
            )
            self.assertEqual(
                __import__("os").environ["ARTHEXIS_DATA_DIR"],
                "/var/lib/arthexis",
            )
            self.assertEqual(
                __import__("os").environ["ARTHEXIS_ALLOWED_HOSTS"],
                "0.0.0.0,arthexis.com",
            )

        execv.assert_called_once_with("/runtime/bin/daphne", ("/runtime/bin/daphne", "daphne-arg"))
