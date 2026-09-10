from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from config.roles import SUPPORTED_ROLES, normalize_role

DEFAULT_INSTALL_ROOT = Path("/opt/arthexis")
DEFAULT_CHECKOUT_NAME = "app"
LOCAL_REDIS_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class InstallationLayout:
    root: Path
    checkout: Path


@dataclass(frozen=True)
class LifecycleOptions:
    role: str | None = None
    site: str | None = None


def layout(root: str | Path | None = None) -> InstallationLayout:
    """Return the application-visible layout for a GWAY-managed installation."""
    selected_root = Path(
        root
        or os.environ.get("ARTHEXIS_INSTALL_ROOT")
        or os.environ.get("ARTHEXIS_MANAGED_ROOT")
        or DEFAULT_INSTALL_ROOT
    ).expanduser()
    return InstallationLayout(
        root=selected_root,
        checkout=selected_root / DEFAULT_CHECKOUT_NAME,
    )


def _resolve_layout(selected: InstallationLayout | None) -> InstallationLayout:
    return selected or layout()


def _managed_runtime_dirs(current: InstallationLayout) -> dict[str, Path]:
    state_root = current.root / "var"
    return {
        "ARTHEXIS_DATA_DIR": state_root / "lib",
        "ARTHEXIS_LOG_DIR": state_root / "log",
        "ARTHEXIS_CACHE_DIR": state_root / "cache",
        "ARTHEXIS_RUN_DIR": state_root / "run",
    }


def _managed_environment(current: InstallationLayout) -> dict[str, str]:
    env = os.environ.copy()
    env["ARTHEXIS_MODE"] = "installed"
    for name, path in _managed_runtime_dirs(current).items():
        env[name] = str(path)
    return env


def _prepare_runtime_state(current: InstallationLayout) -> Path:
    runtime_dirs = _managed_runtime_dirs(current)
    for path in runtime_dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    data_dir = runtime_dirs["ARTHEXIS_DATA_DIR"]
    legacy_database = current.checkout / "db.sqlite3"
    managed_database = data_dir / "db.sqlite3"
    if legacy_database.is_file() and not managed_database.exists():
        shutil.copy2(legacy_database, managed_database)
    return current.root / "var"


def _restore_runtime_ownership(state_root: Path) -> None:
    """Return managed mutable state to the user that invoked sudo."""
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        return

    uid_text = os.environ.get("SUDO_UID", "")
    gid_text = os.environ.get("SUDO_GID", "")
    if not uid_text.isdigit() or not gid_text.isdigit():
        return

    uid = int(uid_text)
    gid = int(gid_text)
    for directory, _, filenames in os.walk(state_root):
        os.chown(directory, uid, gid)
        directory_path = Path(directory)
        for filename in filenames:
            os.chown(directory_path / filename, uid, gid)


def _parse_lifecycle_arguments(arguments: tuple[str, ...]) -> LifecycleOptions:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--role")
    parser.add_argument("--site")
    namespace, unknown = parser.parse_known_args(arguments)
    if unknown:
        parser.error(f"unrecognized arguments: {' '.join(unknown)}")

    role = None
    if namespace.role is not None:
        role = normalize_role(namespace.role)
        if role not in SUPPORTED_ROLES:
            parser.error(
                f"invalid --role {namespace.role!r}; choose from {', '.join(SUPPORTED_ROLES)}"
            )

    return LifecycleOptions(role=role, site=namespace.site)


def _role_lock(current: InstallationLayout) -> Path:
    return current.checkout / ".locks" / "role.lck"


def _persist_role(role: str, current: InstallationLayout) -> None:
    role_lock = _role_lock(current)
    role_lock.parent.mkdir(parents=True, exist_ok=True)
    role_lock.write_text(f"{role}\n", encoding="utf-8")


def _current_role(current: InstallationLayout) -> str:
    try:
        value = _role_lock(current).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return "Terminal"
    return normalize_role(value or "Terminal")


def _local_redis_endpoint(role: str) -> tuple[str, int] | None:
    if normalize_role(role) == "Terminal":
        return None

    broker_url = (
        os.environ.get("CELERY_BROKER_URL", "").strip()
        or os.environ.get("BROKER_URL", "").strip()
        or "redis://localhost:6379/0"
    )
    parsed = urlparse(broker_url)
    if parsed.scheme not in {"redis", "rediss"}:
        return None
    host = parsed.hostname or "localhost"
    if host not in LOCAL_REDIS_HOSTS:
        return None
    return host, parsed.port or 6379


def _redis_is_available(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _os_id() -> str:
    try:
        lines = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ""
    values = dict(
        line.split("=", maxsplit=1)
        for line in lines
        if "=" in line and not line.startswith("#")
    )
    return values.get("ID", "").strip().strip('"').lower()


def _redis_install_commands() -> tuple[str, ...]:
    os_id = _os_id()
    if os_id in {"debian", "ubuntu", "raspbian"}:
        return (
            "sudo apt-get update",
            "sudo apt-get install -y redis-server",
            "sudo systemctl enable --now redis-server",
        )
    if os_id in {"fedora", "rhel", "centos", "rocky", "almalinux"}:
        return (
            "sudo dnf install -y redis",
            "sudo systemctl enable --now redis",
        )
    if os_id == "arch":
        return (
            "sudo pacman -S redis",
            "sudo systemctl enable --now redis",
        )
    return (
        "Install Redis with your operating system package manager.",
        "Start and enable the Redis service.",
    )


def _warn_if_local_redis_missing(role: str) -> None:
    endpoint = _local_redis_endpoint(role)
    if endpoint is None:
        return
    host, port = endpoint
    if _redis_is_available(host, port):
        return

    print(
        f"Arthexis role {role} requires Redis for Celery/Channels, but "
        f"{host}:{port} is unavailable.",
        file=sys.stderr,
    )
    print("Install and start Redis:", file=sys.stderr)
    for command in _redis_install_commands():
        print(f"  {command}", file=sys.stderr)
    print("Verify with:", file=sys.stderr)
    print("  redis-cli ping", file=sys.stderr)
    print("Then rerun the Arthexis install or upgrade.", file=sys.stderr)


def run_python(
    arguments: Iterable[str],
    *,
    layout: InstallationLayout | None = None,
    cwd: str | Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the current interpreter with a deterministic working directory."""
    current = _resolve_layout(layout)
    return subprocess.run(
        [current_python(), *arguments],
        cwd=Path(cwd) if cwd is not None else current.checkout,
        check=check,
        text=True,
        env=_managed_environment(current),
    )


def run_manage(
    command: str,
    *arguments: str,
    layout: InstallationLayout | None = None,
) -> None:
    """Run one Django management command from the installation checkout."""
    current = _resolve_layout(layout)
    run_python(
        ["manage.py", command, *arguments],
        layout=current,
        cwd=current.checkout,
    )


def migrate(*, layout: InstallationLayout | None = None) -> None:
    run_manage("migrate", "--noinput", layout=layout)


def collectstatic(*, layout: InstallationLayout | None = None) -> None:
    run_manage("collectstatic", "--noinput", layout=layout)


def configure_site(domain: str, *, layout: InstallationLayout | None = None) -> None:
    """Configure the canonical site without recursively refreshing the local node."""
    run_manage("site", domain, "--no-refresh-node", layout=layout)


def ensure_local_node(*, layout: InstallationLayout | None = None) -> None:
    """Ensure the current host is registered as the local Arthexis node."""
    run_manage("ensure_local_node", layout=layout)


def prepare(
    *,
    layout: InstallationLayout | None = None,
    site: str | None = None,
    run_migrations: bool = True,
    run_collectstatic: bool = True,
) -> InstallationLayout:
    """Prepare application state for a GWAY-managed installation checkout."""
    current = _resolve_layout(layout)
    if not current.checkout.is_dir():
        raise FileNotFoundError(
            f"installation checkout does not exist: {current.checkout}"
        )

    state_root = _prepare_runtime_state(current)
    try:
        if run_migrations:
            migrate(layout=current)
        if site is not None:
            configure_site(site, layout=current)
        ensure_local_node(layout=current)
        if run_collectstatic:
            collectstatic(layout=current)
    finally:
        _restore_runtime_ownership(state_root)
    return current


def _prepare_for_options(
    arguments: tuple[str, ...],
    *,
    layout: InstallationLayout | None = None,
) -> InstallationLayout:
    current = _resolve_layout(layout)
    options = _parse_lifecycle_arguments(arguments)
    if options.role is not None:
        _persist_role(options.role, current)
    effective_role = options.role or _current_role(current)
    _warn_if_local_redis_missing(effective_role)
    return prepare(layout=current, site=options.site)


def install(
    *arguments: str, layout: InstallationLayout | None = None
) -> InstallationLayout:
    """Application preparation hook for a GWAY installation."""
    return _prepare_for_options(arguments, layout=layout)


def upgrade(
    *arguments: str, layout: InstallationLayout | None = None
) -> InstallationLayout:
    """Application preparation hook for a GWAY upgrade."""
    return _prepare_for_options(arguments, layout=layout)


def current_python() -> str:
    """Return the interpreter currently executing Arthexis."""
    return sys.executable
