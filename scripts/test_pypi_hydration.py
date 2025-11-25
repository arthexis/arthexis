from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    target = Path(tempfile.mkdtemp()) / "site-packages"
    target.mkdir(parents=True, exist_ok=True)

    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            ".",
            "--no-deps",
            "--target",
            str(target),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if install.returncode != 0:
        sys.stderr.write(install.stderr)
        return install.returncode

    env = os.environ.copy()
    env["PYTHONPATH"] = str(target)

    probe_script = (
        "import importlib, json; modules = ['app','arthexis','awg','config','core','nodes','ocpp','pages','protocols','teams','utils'];"
        "results = {name: importlib.import_module(name).__file__ is not None for name in modules};"
        "print(json.dumps(results, sort_keys=True))"
    )
    probe = subprocess.run(
        [sys.executable, "-c", probe_script],
        env=env,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        sys.stderr.write(probe.stderr)
        return probe.returncode

    imports = json.loads(probe.stdout)
    missing = [name for name, ok in imports.items() if not ok]
    if missing:
        sys.stderr.write(f"Missing modules after PyPI-style install: {missing}\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
