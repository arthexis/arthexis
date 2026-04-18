from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path


def _write_fake_manage(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

log_file = Path(os.environ[\"MANAGE_LOG\"])

def _append(entry: str) -> None:
    with log_file.open(\"a\", encoding=\"utf-8\") as handle:
        handle.write(f\"{entry}\\n\")

args = sys.argv[1:]
if args == [\"migrate\", \"--check\"]:
    _append(\"check\")
    raise SystemExit(int(os.environ.get(\"MANAGE_CHECK_EXIT\", \"0\")))
if args == [\"migrate\", \"--noinput\"]:
    _append(\"apply\")
    raise SystemExit(int(os.environ.get(\"MANAGE_APPLY_EXIT\", \"0\")))

raise SystemExit(0)
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_preflight(tmp_path: Path, *, fingerprint: str, metadata: str, db_identity: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base_dir = tmp_path / "project"
    lock_dir = base_dir / ".locks"
    apps_dir = base_dir / "apps" / "demo" / "migrations"
    apps_dir.mkdir(parents=True, exist_ok=True)
    (apps_dir / "0001_initial.py").write_text("# migration fixture\n", encoding="utf-8")
    _write_fake_manage(base_dir / "manage.py")

    script_path = Path(__file__).resolve().parents[3] / "scripts" / "helpers" / "runserver_preflight.sh"
    script = f"""
set -euo pipefail
source {script_path}
compute_migration_fingerprint() {{ printf '%s\\n' \"{fingerprint}\"; }}
compute_migration_metadata_snapshot() {{ printf '%s\\n' '{metadata}'; }}
compute_database_identity() {{ printf '%s\\n' \"{db_identity}\"; }}
run_runserver_preflight
"""

    env = os.environ.copy()
    env.update(
        {
            "BASE_DIR": str(base_dir),
            "LOCK_DIR": str(lock_dir),
            "ARTHEXIS_PYTHON_BIN": sys.executable,
            "MANAGE_LOG": str(lock_dir / "manage.log"),
        }
    )
    if env_extra:
        env.update(env_extra)

    return subprocess.run(
        ["bash", "-lc", script],
        cwd=base_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _manage_calls(lock_dir: Path) -> list[str]:
    log_file = lock_dir / "manage.log"
    if not log_file.exists():
        return []
    return [line.strip() for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_preflight_revalidates_migrate_when_verified_state_matches(tmp_path: Path) -> None:
    base_dir = tmp_path / "project"
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = "fingerprint-v1"
    metadata = json.dumps({"version": 1, "entries": []}, separators=(",", ":"), sort_keys=True)
    db_identity = "db-id-v1"

    (lock_dir / "migrations.sha").write_text(f"{fingerprint}\n", encoding="utf-8")
    (lock_dir / "migrations.meta").write_text(f"{metadata}\n", encoding="utf-8")
    (lock_dir / "migrations.verified.json").write_text(
        json.dumps(
            {
                "version": 1,
                "fingerprint": fingerprint,
                "db_identity": db_identity,
                "status": "success",
                "verified_at": 1,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = _run_preflight(tmp_path, fingerprint=fingerprint, metadata=metadata, db_identity=db_identity)

    assert result.returncode == 0, result.stderr
    assert _manage_calls(lock_dir) == ["check"]


def test_preflight_runs_migrate_check_when_fingerprint_changes(tmp_path: Path) -> None:
    base_dir = tmp_path / "project"
    lock_dir = base_dir / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    cached_metadata = json.dumps({"version": 1, "entries": []}, separators=(",", ":"), sort_keys=True)
    metadata = json.dumps({"version": 1, "entries": [["apps/demo/migrations/0001_initial.py", 2, 20]]}, separators=(",", ":"), sort_keys=True)
    (lock_dir / "migrations.sha").write_text("old-fingerprint\n", encoding="utf-8")
    (lock_dir / "migrations.meta").write_text(f"{cached_metadata}\n", encoding="utf-8")
    (lock_dir / "migrations.verified.json").write_text(
        json.dumps(
            {
                "version": 1,
                "fingerprint": "old-fingerprint",
                "db_identity": "db-id-v1",
                "status": "success",
                "verified_at": 1,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = _run_preflight(
        tmp_path,
        fingerprint="new-fingerprint",
        metadata=metadata,
        db_identity="db-id-v1",
    )

    assert result.returncode == 0, result.stderr
    assert _manage_calls(lock_dir) == ["check"]


def test_preflight_check_policy_fails_fast_for_pending_migrations(tmp_path: Path) -> None:
    metadata = json.dumps({"version": 1, "entries": []}, separators=(",", ":"), sort_keys=True)

    result = _run_preflight(
        tmp_path,
        fingerprint="fingerprint-v1",
        metadata=metadata,
        db_identity="db-id-v1",
        env_extra={
            "ARTHEXIS_MIGRATION_POLICY": "check",
            "MANAGE_CHECK_EXIT": "1",
        },
    )

    lock_dir = tmp_path / "project" / ".locks"
    assert result.returncode != 0
    assert _manage_calls(lock_dir) == ["check"]
