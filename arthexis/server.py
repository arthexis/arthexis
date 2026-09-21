"""Runtime server entrypoint for supervised Arthexis deployments."""

import os
from pathlib import Path


def daphne_command(*, host="127.0.0.1", port=8888):
    """Return the Daphne command for the current Python environment."""
    executable = Path(os.environ.get("VIRTUAL_ENV", "")) / "bin" / "daphne"
    if not executable.is_file():
        executable = Path(os.sys.executable).with_name("daphne")
    return (
        str(executable),
        "-b",
        str(host),
        "-p",
        str(int(port)),
        "arthexis.asgi:application",
    )


def main(
    host: str = "127.0.0.1",
    port: int = 8888,
    data_dir: str = "/var/lib/arthexis",
) -> None:
    """Replace this process with Daphne serving the Arthexis ASGI application."""
    os.environ["ARTHEXIS_DATA_DIR"] = str(Path(data_dir).expanduser())
    command = daphne_command(host=host, port=port)
    os.execv(command[0], command)
