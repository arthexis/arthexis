import os
import runpy
import sys


def _strip_debugpy() -> None:
    os.environ.pop("DEBUGPY_LAUNCHER_PORT", None)
    if "PYTHONPATH" in os.environ:
        os.environ["PYTHONPATH"] = os.pathsep.join(
            p for p in os.environ["PYTHONPATH"].split(os.pathsep) if "debugpy" not in p
        )


def main(argv: list[str] | None = None) -> None:
    """Entry point for VS Code debugger.

    Removes debugpy hooks and proxies execution to ``manage.py``.
    """

    _strip_debugpy()

    if argv is None:
        argv = sys.argv[1:]
    sys.argv = ["manage.py", *argv]
    runpy.run_path("manage.py", run_name="__main__")


if __name__ == "__main__":  # pragma: no cover - script entry
    main()
