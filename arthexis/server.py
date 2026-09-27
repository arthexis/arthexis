"""Runtime server entrypoint for supervised Arthexis deployments."""

import argparse
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
    allowed_hosts: str = "0.0.0.0",
) -> None:
    """Replace this process with Daphne serving the Arthexis ASGI application."""
    os.environ["ARTHEXIS_DATA_DIR"] = str(Path(data_dir).expanduser())
    os.environ["ARTHEXIS_ALLOWED_HOSTS"] = allowed_hosts
    command = daphne_command(host=host, port=port)
    os.execv(command[0], command)


def cli_main() -> None:
    """Run the standalone Arthexis server CLI without requiring GWAY."""
    parser = argparse.ArgumentParser(description="Serve the Arthexis ASGI application.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--data-dir", default="/var/lib/arthexis")
    parser.add_argument("--allowed-hosts", default="0.0.0.0")
    arguments = parser.parse_args()
    main(
        host=arguments.host,
        port=arguments.port,
        data_dir=arguments.data_dir,
        allowed_hosts=arguments.allowed_hosts,
    )


if __name__ == "__main__":
    cli_main()
