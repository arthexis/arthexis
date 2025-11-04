import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from tests.conftest import create_stub_venv

REPO_ROOT = Path(__file__).resolve().parent.parent


def clone_repo(tmp_path: Path) -> Path:
    clone_dir = tmp_path / "repo"
    shutil.copytree(REPO_ROOT, clone_dir)
    return clone_dir


def test_env_refresh_installs_pip(tmp_path: Path) -> None:
    repo = clone_repo(tmp_path)
    stub = create_stub_venv(repo)

    requirements = repo / "requirements.txt"
    requirements.write_text("")
    empty_hash = hashlib.md5(b"").hexdigest()
    (repo / "requirements.md5").write_text(empty_hash)

    # replace env-refresh.py with a no-op to avoid heavy imports
    (repo / "env-refresh.py").write_text("if __name__ == '__main__':\n    pass\n")

    env = os.environ.copy()
    env.update({
        "FAILOVER_CREATED": "1",
        "STUB_PYTHON_REAL": sys.executable,
    })

    result = subprocess.run(["bash", "env-refresh.sh"], cwd=repo, env=env)
    assert result.returncode == 0

    entries = [
        json.loads(line)
        for line in stub.log.read_text().splitlines()
        if line.strip()
    ]

    ensurepip_calls = [
        entry
        for entry in entries
        if entry.get("kind") == "module" and entry.get("module") == "ensurepip"
    ]
    assert ensurepip_calls, "env-refresh.sh did not attempt to install pip with ensurepip"
    assert all(entry.get("handled") for entry in ensurepip_calls)
    assert all(not entry.get("passthrough") for entry in ensurepip_calls)

    pip_version_checks = [
        entry
        for entry in entries
        if entry.get("kind") == "module"
        and entry.get("module") == "pip"
        and entry.get("args") == ["--version"]
    ]
    assert pip_version_checks, "env-refresh.sh did not probe pip availability"
    assert all(not entry.get("passthrough") for entry in entries)
