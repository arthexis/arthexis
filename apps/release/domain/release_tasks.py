from __future__ import annotations

import importlib.util
import json
import hashlib
import hmac
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from apps.release import git_utils

RELEASE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

def _run(
    cmd: Iterable[str], *, check: bool = True, cwd: Path | str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), check=check, cwd=cwd)


def _authed_remote_url(remote: str, *, base_dir: Path | None = None) -> str | None:
    token = (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or ""
    ).strip()
    if not token:
        return None
    url = git_utils.git_remote_url(remote, base_dir=base_dir, use_push_url=True)
    if not url:
        url = git_utils.git_remote_url(remote, base_dir=base_dir)
    if not url:
        return None
    return git_utils.remote_url_with_credentials(
        url,
        username="x-access-token",
        password=token,
    )


def _git_push(remote: str, *refs: str, base_dir: Path | None = None) -> None:
    authed_remote = _authed_remote_url(remote, base_dir=base_dir)
    target_remote = authed_remote or remote
    _run(["git", "push", target_remote, *refs], cwd=base_dir)


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
        _git_push("origin", maintenance_branch, base_dir=base_dir)



def capture_migration_state(version: str, base_dir: Path | None = None) -> Path:
    """Capture release migration artifacts, graph snapshots, and checksums."""

    version = version.strip()
    if not RELEASE_VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Invalid release version: {version!r}")

    base_dir = base_dir or Path.cwd()
    out_dir = base_dir / "releases" / version
    migrations_dir = out_dir / "migrations"
    manifests_dir = migrations_dir / "manifests"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)

    plan = subprocess.check_output(
        ["python", "manage.py", "showmigrations", "--plan"], text=True, cwd=base_dir
    )
    (out_dir / "migration-plan.txt").write_text(plan)

    inspect = subprocess.check_output(["python", "manage.py", "inspectdb"], text=True, cwd=base_dir)
    (out_dir / "inspectdb.py").write_text(inspect)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    artifact_paths: list[Path] = [out_dir / "migration-plan.txt", out_dir / "inspectdb.py"]
    if importlib.util.find_spec("django") and shutil.which("pg_dump"):
        import django
        from django.conf import settings
        from django.db.migrations.loader import MigrationLoader
        from django.db.migrations.operations.special import RunPython, RunSQL
        from django.db import connections

        django.setup()
        connection = connections["default"]
        loader = MigrationLoader(connection, ignore_no_migrations=True)

        app_dag: dict[str, dict[str, object]] = {}
        for app_label in sorted(loader.migrated_apps):
            migrations = sorted(
                name for migration_app, name in loader.disk_migrations if migration_app == app_label
            )
            nodes: dict[str, dict[str, list[str]]] = {}
            for migration_name in migrations:
                migration = loader.disk_migrations[(app_label, migration_name)]
                dependencies = [
                    dep_name for dep_app, dep_name in migration.dependencies if dep_app == app_label
                ]
                children = [
                    child_name
                    for child_app, child_name in loader.graph.node_map[(app_label, migration_name)].children
                    if child_app == app_label
                ]
                nodes[migration_name] = {
                    "dependencies": sorted(dependencies),
                    "children": sorted(children),
                }

            app_dag[app_label] = {
                "leaf_nodes": sorted(name for app, name in loader.graph.leaf_nodes(app_label)),
                "nodes": nodes,
            }

        snapshot = {"version": version, "apps": app_dag}
        snapshot_path = migrations_dir / "snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        artifact_paths.append(snapshot_path)

        for prior_snapshot in sorted((base_dir / "releases").glob("*/migrations/snapshot.json")):
            prior_version = prior_snapshot.parent.parent.name
            if prior_version == version:
                continue
            prior_payload = json.loads(prior_snapshot.read_text())
            deltas: dict[str, list[str]] = {}
            for app_label, app_payload in app_dag.items():
                previous_nodes = set(prior_payload.get("apps", {}).get(app_label, {}).get("nodes", {}).keys())
                delta = sorted(name for name in app_payload["nodes"].keys() if name not in previous_nodes)
                if delta:
                    deltas[app_label] = delta

            manifest = {
                "from_version": prior_version,
                "to_version": version,
                "deltas": deltas,
            }
            manifest_name = f"{prior_version}__to__{version}.json"
            manifest_path = manifests_dir / manifest_name
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            artifact_paths.append(manifest_path)

            for app_label, migration_names in deltas.items():
                for migration_name in migration_names:
                    migration = loader.disk_migrations[(app_label, migration_name)]
                    if any(isinstance(operation, (RunPython, RunSQL)) for operation in migration.operations):
                        continue
                    sql_path = manifests_dir / f"{prior_version}__to__{version}.{app_label}.{migration_name}.sql"
                    try:
                        sql = subprocess.check_output(
                            ["python", "manage.py", "sqlmigrate", app_label, migration_name],
                            text=True,
                            cwd=base_dir,
                        )
                    except subprocess.CalledProcessError:
                        continue
                    sql_path.write_text(sql)
                    artifact_paths.append(sql_path)

        db_name = settings.DATABASES["default"].get("NAME", "")
        if db_name:
            schema_path = out_dir / "schema.sql"
            with schema_path.open("w") as fh:
                subprocess.run(["pg_dump", "--schema-only", db_name], check=True, stdout=fh)
            artifact_paths.append(schema_path)

    checksum_lines: list[str] = []
    for artifact in sorted(artifact_paths):
        if not artifact.exists():
            continue
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        relative_path = Path(os.path.relpath(artifact, migrations_dir))
        checksum_lines.append(f"{digest}  {relative_path.as_posix()}")

    checksums_path = migrations_dir / "checksums.sha256"
    checksums_path.write_text("\n".join(checksum_lines) + "\n")
    artifact_paths.append(checksums_path)

    signing_key = (
        os.environ.get("RELEASE_BUNDLE_SIGNING_KEY")
        or os.environ.get("ARTHEXIS_RELEASE_BUNDLE_SIGNING_KEY")
        or ""
    ).strip()
    if signing_key:
        signature = hmac.new(
            signing_key.encode("utf-8"), checksums_path.read_bytes(), hashlib.sha256
        ).hexdigest()
        signature_path = migrations_dir / "checksums.sha256.sig"
        signature_path.write_text(signature + "\n")
        artifact_paths.append(signature_path)

    _run(["git", "add", *map(str, artifact_paths)], cwd=base_dir)
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
    _git_push("origin", "main", "--tags", base_dir=base_dir)
