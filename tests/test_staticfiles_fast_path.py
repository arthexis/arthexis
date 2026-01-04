from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER_PATH = REPO_ROOT / "scripts" / "helpers" / "staticfiles.sh"


def _write_fake_staticfiles_hasher(base_dir: Path) -> Path:
    script_path = base_dir / "fake_staticfiles_md5.py"
    script_path.write_text(
        textwrap.dedent(
            """#!/usr/bin/env python
import argparse
import hashlib
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--metadata-output")
parser.add_argument("--ignore-cache", action="store_true")
args = parser.parse_args()

root = Path(os.environ.get("STATIC_ROOT", Path.cwd() / "static"))
root.mkdir(parents=True, exist_ok=True)

digest = hashlib.md5()
latest = 0
file_count = 0
for file_path in sorted(root.rglob("*")):
    if not file_path.is_file():
        continue
    digest.update(str(file_path.relative_to(root)).encode("utf-8", "ignore"))
    digest.update(b"-")
    data = file_path.read_bytes()
    digest.update(data)
    stat = file_path.stat()
    latest = max(latest, stat.st_mtime_ns)
    file_count += 1

metadata = {
    "hash": digest.hexdigest(),
    "commit": "",
    "latest_mtime_ns": latest if file_count else 0,
    "file_count": file_count,
    "roots": [str(root)],
    "cacheable": True,
}

log_path = os.environ.get("HASH_LOG")
if log_path:
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(metadata["hash"] + "\\n")

if args.metadata_output:
    Path(args.metadata_output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.metadata_output, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle)

print(metadata["hash"])
"""
        )
    )
    script_path.chmod(0o755)
    return script_path


def _run_static_hash(base_dir: Path, hash_script: Path, *, force: bool = False) -> dict:
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    static_dir = base_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    hash_output_path = base_dir / "hash_output.txt"
    hash_log_path = base_dir / "hash.log"

    env = os.environ.copy()
    env.update(
        {
            "BASE_DIR": str(base_dir),
            "LOCK_DIR": str(lock_dir),
            "STATICFILES_HASH_SCRIPT": str(hash_script),
            "HASH_LOG": str(hash_log_path),
        }
    )

    force_flag = "true" if force else "false"
    command = "\n".join(
        [
            "set -e",
            f"cd '{base_dir}'",
            f"source '{HELPER_PATH}'",
            'STATIC_MD5_FILE="$LOCK_DIR/staticfiles.md5"',
            'STATIC_META_FILE="$LOCK_DIR/staticfiles.meta"',
            f'hash_value=$(arthexis_prepare_staticfiles_hash "$STATIC_MD5_FILE" "$STATIC_META_FILE" {force_flag})',
            f'printf "%s" "$hash_value" > "{hash_output_path}"',
        ]
    )

    subprocess.run(["bash", "-c", command], check=True, env=env)

    return {
        "hash": hash_output_path.read_text(),
        "log_entries": hash_log_path.read_text().splitlines() if hash_log_path.exists() else [],
    }


def test_staticfiles_hash_fast_path_reuses_lock(tmp_path: Path):
    hasher = _write_fake_staticfiles_hasher(tmp_path)
    static_dir = tmp_path / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "app.txt").write_text("hello", encoding="utf-8")

    first_run = _run_static_hash(tmp_path, hasher)
    second_run = _run_static_hash(tmp_path, hasher)

    assert first_run["hash"] == second_run["hash"]
    assert len(first_run["log_entries"]) == 1
    assert second_run["log_entries"] == first_run["log_entries"]


def test_staticfiles_hash_invalidates_on_change(tmp_path: Path):
    hasher = _write_fake_staticfiles_hasher(tmp_path)
    static_dir = tmp_path / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    target_file = static_dir / "app.txt"
    target_file.write_text("hello", encoding="utf-8")

    initial = _run_static_hash(tmp_path, hasher)
    target_file.write_text("updated", encoding="utf-8")

    refreshed = _run_static_hash(tmp_path, hasher)

    assert len(initial["log_entries"]) == 1
    assert len(refreshed["log_entries"]) == 2
    assert refreshed["hash"] != initial["hash"]
