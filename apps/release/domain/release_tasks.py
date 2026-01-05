from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable


def _run(
    cmd: Iterable[str], *, check: bool = True, cwd: Path | str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), check=check, cwd=cwd)


def _ignored_working_tree_paths(base_dir: Path) -> set[Path]:
    ignored: set[Path] = set()
    base_dir = base_dir.resolve()

    env_log_dir = os.environ.get("ARTHEXIS_LOG_DIR")
    if env_log_dir:
        try:
            log_dir = Path(env_log_dir).expanduser().resolve()
        except OSError:
            log_dir = None
        else:
            try:
                log_dir.relative_to(base_dir)
            except ValueError:
                pass
            else:
                ignored.add(log_dir)

    for path in (base_dir / "logs", base_dir / ".locks"):
        ignored.add(path.resolve())

    return ignored


def _has_porcelain_changes(output: str, *, base_dir: Path | None = None) -> bool:
    base_dir = (base_dir or Path.cwd()).resolve()
    ignored_paths = _ignored_working_tree_paths(base_dir)

    for line in output.splitlines():
        if not line or line.startswith("##"):
            continue

        entry = line[3:].split(" -> ", 1)[-1].strip()
        try:
            entry_path = (base_dir / entry).resolve()
        except Exception:
            return True

        if any(
            entry_path == ignored or entry_path.is_relative_to(ignored)
            for ignored in ignored_paths
        ):
            continue

        return True
    return False


def _is_clean_repository(base_dir: Path | None = None) -> bool:
    proc = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=base_dir
    )
    return not _has_porcelain_changes(proc.stdout, base_dir=base_dir)


def _maybe_create_maintenance_branch(
    previous: str | None, current: str, *, base_dir: Path | None = None
) -> None:
    if not previous:
        return

    try:
        prev_major, prev_minor, *_ = previous.split(".")
        curr_major, curr_minor, *_ = current.split(".")
    except ValueError:
        return

    if prev_major != curr_major or prev_minor == curr_minor:
        return

    maintenance_branch = f"release/v{prev_major}.{prev_minor}"
    exists_locally = (
        subprocess.call(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{maintenance_branch}"],
            cwd=base_dir,
        )
        == 0
    )
    if not exists_locally:
        _run(["git", "branch", maintenance_branch], cwd=base_dir)

    remote_exists = (
        subprocess.call(
            ["git", "ls-remote", "--exit-code", "--heads", "origin", maintenance_branch],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=base_dir,
        )
        == 0
    )
    if not remote_exists:
        _run(["git", "push", "origin", maintenance_branch], cwd=base_dir)


def capture_migration_state(version: str, base_dir: Path | None = None) -> Path:
    base_dir = base_dir or Path.cwd()
    out_dir = base_dir / "releases" / version
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = subprocess.check_output(
        ["python", "manage.py", "showmigrations", "--plan"], text=True, cwd=base_dir
    )
    (out_dir / "migration-plan.txt").write_text(plan)

    inspect = subprocess.check_output(["python", "manage.py", "inspectdb"], text=True, cwd=base_dir)
    (out_dir / "inspectdb.py").write_text(inspect)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    if importlib.util.find_spec("django") and shutil.which("pg_dump"):
        import django
        from django.conf import settings

        django.setup()
        db_name = settings.DATABASES["default"].get("NAME", "")
        if db_name:
            schema_path = out_dir / "schema.sql"
            with schema_path.open("w") as fh:
                subprocess.run(["pg_dump", "--schema-only", db_name], check=True, stdout=fh)

    files = [out_dir / "migration-plan.txt", out_dir / "inspectdb.py"]
    schema_file = out_dir / "schema.sql"
    if schema_file.exists():
        files.append(schema_file)
    _run(["git", "add", *map(str, files)], cwd=base_dir)
    return out_dir


def prepare_release(version: str, *, base_dir: Path | None = None) -> None:
    base_dir = base_dir or Path.cwd()
    version_file = base_dir / "VERSION"

    if not _is_clean_repository(base_dir):
        raise RuntimeError("Working tree or index is dirty; please commit or stash changes before releasing.")

    previous_version = (
        subprocess.run(
            ["git", "show", "HEAD:VERSION"], capture_output=True, text=True, cwd=base_dir
        )
        .stdout.strip()
    )

    _maybe_create_maintenance_branch(previous_version, version, base_dir=base_dir)
    version_file.write_text(f"{version}\n")

    capture_migration_state(version, base_dir=base_dir)

    release_dir = base_dir / "releases" / version
    _run(["git", "add", str(version_file), str(release_dir)], cwd=base_dir)
    _run(["git", "commit", "-m", f"Release {version}"], cwd=base_dir)

    archive_path = release_dir / "source.tar.gz"
    _run(
        ["git", "archive", "--format=tar.gz", "-o", str(archive_path), "HEAD"],
        check=True,
        cwd=base_dir,
    )

    _run(["git", "add", str(archive_path)], cwd=base_dir)
    _run(["git", "commit", "--amend", "--no-edit"], cwd=base_dir)

    _run(["git", "tag", "-a", f"v{version}", "-m", f"Release {version}"], cwd=base_dir)
    _run(["git", "push", "origin", "main", "--tags"], cwd=base_dir)
