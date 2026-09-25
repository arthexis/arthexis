import os
from unittest.mock import patch

from arthexis.server import daphne_command, main


def test_daphne_command_binds_locally_by_default() -> None:
    command = daphne_command()

    assert command[-5:] == (
        "-b",
        "127.0.0.1",
        "-p",
        "8888",
        "arthexis.asgi:application",
    )


@patch("arthexis.server.os.execv")
@patch("arthexis.server.daphne_command")
def test_main_sets_persistent_data_dir_before_exec(command, execv) -> None:
    command.return_value = ("/runtime/bin/daphne", "daphne-arg")

    with patch.dict("arthexis.server.os.environ", {}, clear=True):
        main(
            data_dir="/var/lib/arthexis",
            allowed_hosts="0.0.0.0,arthexis.com",
        )
        assert os.environ["ARTHEXIS_DATA_DIR"] == "/var/lib/arthexis"
        assert os.environ["ARTHEXIS_ALLOWED_HOSTS"] == "0.0.0.0,arthexis.com"

    execv.assert_called_once_with(
        "/runtime/bin/daphne",
        ("/runtime/bin/daphne", "daphne-arg"),
    )
