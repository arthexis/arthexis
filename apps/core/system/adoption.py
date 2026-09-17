from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import uuid
from pathlib import Path

from config.roles import SUPPORTED_ROLES, normalize_role


class AdoptionExecutionError(RuntimeError):
    """Raised when an adoption plan cannot be executed safely."""


def _git(source: Path, *arguments: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "--no-optional-locks",
            "-C",
            str(source),
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_inventory(
    source: Path, blockers: list[str]
) -> tuple[str | None, str | None, bool | None]:
    try:
        root = Path(_git(source, "rev-parse", "--show-toplevel")).resolve()
        if root != source:
            blockers.append(f"adoption source must be the repository root: {root}")
        revision = _git(source, "rev-parse", "HEAD")
        branch = _git(source, "branch", "--show-current") or None
        dirty = bool(_git(source, "status", "--porcelain=v1", "--untracked-files=all"))
    except (OSError, subprocess.CalledProcessError) as exc:
        blockers.append(f"cannot inspect source Git checkout: {exc}")
        return None, None, None
    return revision, branch, dirty


def _source_role(source: Path, blockers: list[str]) -> str:
    role_path = source / ".locks" / "role.lck"
    try:
        value = role_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "Terminal"
    except (OSError, UnicodeError) as exc:
        blockers.append(f"cannot read source node role: {exc}")
        return "Terminal"

    role = normalize_role(value or "Terminal")
    if role not in SUPPORTED_ROLES:
        blockers.append(f"unsupported source node role: {value!r}")
        return role
    return role


def _transfer_item(
    kind: str,
    source: Path | None,
    target: Path,
    classification: str,
    detail: str,
) -> dict[str, object]:
    return {
        "kind": kind,
        "source": str(source) if source is not None else None,
        "target": str(target),
        "classification": classification,
        "detail": detail,
    }


def inspect_adoption(
    source: str | Path,
    *,
    target_root: str | Path = "/opt/arthexis",
    allow_managed_scaffold: bool = False,
) -> dict[str, object]:
    """Build a read-only plan for adopting one unmanaged Arthexis checkout."""
    source_path = Path(source).expanduser().resolve()
    root = Path(target_root).expanduser().resolve()
    target_checkout = root / "app"
    target_environment = root / ".venv"
    target_data = root / "var" / "lib"
    blockers: list[str] = []

    if not source_path.is_dir():
        blockers.append(f"adoption source is not a directory: {source_path}")
    if source_path == target_checkout.resolve():
        blockers.append("adoption source is already the canonical managed checkout")

    manage_py = source_path / "manage.py"
    if source_path.is_dir() and not manage_py.is_file():
        blockers.append(f"source does not look like an Arthexis checkout: missing {manage_py}")

    revision: str | None = None
    branch: str | None = None
    dirty: bool | None = None
    if source_path.is_dir():
        revision, branch, dirty = _git_inventory(source_path, blockers)

    version_path = source_path / "VERSION"
    try:
        version = version_path.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        version = None
    except (OSError, UnicodeError) as exc:
        blockers.append(f"cannot read source VERSION: {exc}")
        version = None

    role = _source_role(source_path, blockers) if source_path.is_dir() else "Terminal"
    env_files = sorted(path.name for path in source_path.glob("*.env") if path.is_file())

    transfers: list[dict[str, object]] = []
    database = source_path / "db.sqlite3"
    sqlite_sidecars = [
        path
        for path in (
            source_path / "db.sqlite3-wal",
            source_path / "db.sqlite3-shm",
        )
        if path.is_file()
    ]
    if database.is_file():
        transfers.append(
            _transfer_item(
                "database",
                database,
                target_data / "db.sqlite3",
                "sqlite-backup",
                "Create a consistent SQLite backup into managed persistent data.",
            )
        )
    else:
        if sqlite_sidecars:
            blockers.append(
                "SQLite WAL/SHM sidecar state exists without db.sqlite3; restore or remove the orphan sidecars before adoption"
            )
        transfers.append(
            _transfer_item(
                "database",
                None,
                target_data / "db.sqlite3",
                "regenerable",
                "No checkout-local SQLite database was detected; managed setup will use configured database state.",
            )
        )

    for name in ("media", "uploads"):
        candidate = source_path / name
        if candidate.is_dir():
            transfers.append(
                _transfer_item(
                    name,
                    candidate,
                    target_data / name,
                    "copyable",
                    f"Copy persistent {name} content into managed data.",
                )
            )

    virtual_environments = [
        str(path)
        for name in (".venv", "venv")
        if (path := source_path / name).is_dir()
    ]
    transfers.append(
        _transfer_item(
            "python-environment",
            Path(virtual_environments[0]) if virtual_environments else None,
            target_environment,
            "regenerable",
            "Rebuild the managed Python environment from the trusted managed checkout; do not copy the developer virtual environment.",
        )
    )

    for env_name in env_files:
        transfers.append(
            _transfer_item(
                "environment-config",
                source_path / env_name,
                target_data / "config" / env_name,
                "copyable",
                "Preserve the configuration file without exposing its values in preflight output.",
            )
        )

    transfers.append(
        _transfer_item(
            "services",
            None,
            root / "services",
            "regenerable",
            "Developer processes and service definitions are not transferred; GWAY recreates the managed service topology from the adopted node role.",
        )
    )

    ownership_marker = root / ".gway" / "arthexis.json"
    if not allow_managed_scaffold:
        if target_checkout.exists():
            blockers.append(f"managed target checkout already exists: {target_checkout}")
        if target_environment.exists():
            blockers.append(f"managed target environment already exists: {target_environment}")
    else:
        if not target_checkout.is_dir():
            blockers.append(f"GWAY managed checkout is missing: {target_checkout}")
        if not target_environment.is_dir():
            blockers.append(f"GWAY managed environment is missing: {target_environment}")
    if ownership_marker.exists():
        blockers.append(f"managed ownership metadata already exists: {ownership_marker}")
    if target_data.exists() and any(target_data.iterdir()):
        blockers.append(f"managed persistent data is not empty: {target_data}")

    return {
        "mode": "adoption-preflight",
        "ready": not blockers,
        "source": str(source_path),
        "target_root": str(root),
        "target_checkout": str(target_checkout),
        "target_environment": str(target_environment),
        "target_persistent_data": str(target_data),
        "revision": revision,
        "branch": branch,
        "dirty": dirty,
        "version": version,
        "role": role,
        "environment_files": env_files,
        "virtual_environments": virtual_environments,
        "sqlite_sidecars": [str(path) for path in sqlite_sidecars],
        "transfers": transfers,
        "blockers": blockers,
        "notes": [
            "Source code and local modifications remain in the developer checkout and are not copied into the managed checkout.",
            "Preflight reports environment file names only; it does not read or print secret values.",
        ],
    }


def _reject_symlinks(source: Path) -> None:
    if source.is_symlink():
        raise AdoptionExecutionError(f"adoption transfer source is a symlink: {source}")
    if source.is_dir():
        for child in source.rglob("*"):
            if child.is_symlink():
                raise AdoptionExecutionError(
                    f"adoption transfer tree contains a symlink: {child}"
                )


def _copy_transfer(source: Path, target: Path) -> None:
    _reject_symlinks(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _snapshot_sqlite(source: Path, target: Path) -> None:
    _reject_symlinks(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with (
            sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_db,
            sqlite3.connect(target) as target_db,
        ):
            source_db.backup(target_db)
    except sqlite3.Error as exc:
        raise AdoptionExecutionError(
            f"cannot create consistent SQLite adoption snapshot from {source}: {exc}"
        ) from exc


def execute_adoption(
    source: str | Path,
    *,
    target_root: str | Path = "/opt/arthexis",
    allow_dirty_source: bool = False,
) -> dict[str, object]:
    """Transfer durable unmanaged state into a GWAY-created managed scaffold."""
    plan = inspect_adoption(
        source,
        target_root=target_root,
        allow_managed_scaffold=True,
    )
    blockers = list(plan["blockers"])
    if plan["dirty"] and not allow_dirty_source:
        blockers.append(
            "source checkout has uncommitted changes; rerun with --allow-dirty-source to acknowledge that source changes are not copied"
        )
    if blockers:
        raise AdoptionExecutionError("; ".join(blockers))

    root = Path(str(plan["target_root"]))
    target_data = Path(str(plan["target_persistent_data"]))
    staging = root / ".gway" / f"adoption-staging-{uuid.uuid4().hex}"
    staged_data = staging / "data"
    created: list[Path] = []

    try:
        staged_data.mkdir(parents=True)
        for item in plan["transfers"]:
            if item["source"] is None:
                continue
            source_path = Path(str(item["source"]))
            target_path = Path(str(item["target"]))
            relative = target_path.relative_to(target_data)
            if item["classification"] == "sqlite-backup":
                _snapshot_sqlite(source_path, staged_data / relative)
            elif item["classification"] == "copyable":
                _copy_transfer(source_path, staged_data / relative)

        provenance = {
            "source": plan["source"],
            "revision": plan["revision"],
            "branch": plan["branch"],
            "version": plan["version"],
            "dirty_source_acknowledged": bool(plan["dirty"]),
            "role": plan["role"],
        }
        (staged_data / "adoption.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        target_data.mkdir(parents=True, exist_ok=True)
        if any(target_data.iterdir()):
            raise AdoptionExecutionError(
                f"managed persistent data became non-empty during adoption: {target_data}"
            )
        for staged in sorted(staged_data.iterdir(), key=lambda path: path.name):
            target = target_data / staged.name
            created.append(target)
            staged.replace(target)
    except BaseException:
        for target in reversed(created):
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return plan


def rollback_adoption(plan: dict[str, object]) -> None:
    """Remove state created by a failed adoption lifecycle transaction."""
    target_data = Path(str(plan["target_persistent_data"]))
    provenance = target_data / "adoption.json"
    if not provenance.is_file():
        return
    for child in list(target_data.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
