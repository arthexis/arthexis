from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from apps.nginx.config_utils import MAINTENANCE_ROOT
from apps.nginx.renderers import generate_unified_config

SITES_AVAILABLE_DIR = Path("/etc/nginx/sites-available")
SITES_ENABLED_DIR = Path("/etc/nginx/sites-enabled")
MAINTENANCE_DEST_DIR = Path(MAINTENANCE_ROOT)


@dataclass
class ApplyResult:
    changed: bool
    validated: bool
    reloaded: bool
    message: str


class NginxUnavailableError(Exception):
    """Raised when nginx or prerequisites are not available."""


class ValidationError(Exception):
    """Raised when nginx validation fails."""


def _maintenance_assets_dir() -> Path:
    return Path(settings.BASE_DIR) / "config" / "data" / "nginx" / "maintenance"


def ensure_nginx_in_path() -> bool:
    if shutil.which("nginx"):
        return True

    extra_paths = ["/usr/sbin", "/usr/local/sbin", "/sbin"]
    for directory in extra_paths:
        candidate = Path(directory) / "nginx"
        if candidate.exists() and candidate.is_file():
            current_path = os.environ.get("PATH", "")
            if str(directory) not in current_path.split(":"):
                os.environ["PATH"] = f"{current_path}:{directory}" if current_path else str(directory)
            return True

    return False


def can_manage_nginx() -> bool:
    if not shutil.which("sudo"):
        return False
    if ensure_nginx_in_path():
        return True
    if Path("/etc/nginx").exists():
        return True
    return False


def reload_or_start_nginx(sudo: str = "sudo") -> bool:
    reload_result = subprocess.run([sudo, "systemctl", "reload", "nginx"], check=False)
    if reload_result.returncode == 0:
        return True

    start_result = subprocess.run([sudo, "systemctl", "start", "nginx"], check=False)
    return start_result.returncode == 0


def _ensure_site_enabled(source: Path, *, sudo: str = "sudo") -> None:
    if source.parent != SITES_AVAILABLE_DIR:
        return

    enabled_path = SITES_ENABLED_DIR / source.name
    subprocess.run([sudo, "mkdir", "-p", str(SITES_ENABLED_DIR)], check=False)
    subprocess.run([sudo, "ln", "-sf", str(source), str(enabled_path)], check=True)


def _write_lock(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def record_lock_state(mode: str, port: int, role: str) -> None:
    base_dir = Path(settings.BASE_DIR)
    lock_dir = base_dir / ".locks"
    _write_lock(lock_dir / "nginx_mode.lck", mode)
    _write_lock(lock_dir / "backend_port.lck", str(port))
    _write_lock(lock_dir / "role.lck", role)


def remove_nginx_configuration(*, sudo: str = "sudo", reload: bool = True) -> ApplyResult:
    if not can_manage_nginx():
        raise NginxUnavailableError(
            "nginx configuration requires sudo privileges and nginx assets. "
            "Install nginx with 'sudo apt update && sudo apt install nginx'."
        )

    commands = [
        [sudo, "sh", "-c", "rm -f /etc/nginx/sites-enabled/arthexis*.conf"],
        [sudo, "sh", "-c", "rm -f /etc/nginx/sites-available/arthexis*.conf"],
        [sudo, "sh", "-c", "rm -f /etc/nginx/conf.d/arthexis-*.conf"],
    ]
    for cmd in commands:
        subprocess.run(cmd, check=False)

    validated = False
    reloaded = False
    if reload and ensure_nginx_in_path() and shutil.which("nginx"):
        test_result = subprocess.run([sudo, "nginx", "-t"], check=False)
        validated = test_result.returncode == 0
        if validated:
            reloaded = reload_or_start_nginx(sudo)

    return ApplyResult(changed=True, validated=validated, reloaded=reloaded, message="Removed nginx configuration.")


def _write_config_with_sudo(dest: Path, content: str, *, sudo: str = "sudo") -> None:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as temp_file:
        temp_file.write(content)
        temp_path = Path(temp_file.name)

    try:
        subprocess.run([sudo, "mkdir", "-p", str(dest.parent)], check=True)
        quoted_temp = shlex.quote(str(temp_path))
        quoted_dest = shlex.quote(str(dest))
        subprocess.run(
            [sudo, "sh", "-c", f"cat {quoted_temp} > {quoted_dest}"],
            check=True,
        )
    finally:
        temp_path.unlink(missing_ok=True)


def _ensure_maintenance_assets(*, sudo: str = "sudo") -> None:
    assets_dir = _maintenance_assets_dir()
    if not assets_dir.is_dir():
        return
    subprocess.run([sudo, "mkdir", "-p", str(MAINTENANCE_DEST_DIR)], check=True)
    subprocess.run(
        [sudo, "cp", "-r", f"{assets_dir}/.", str(MAINTENANCE_DEST_DIR)],
        check=True,
    )


def _disable_default_site_for_public_mode(
    *,
    mode: str,
    allow_remove_default_site: bool,
    sudo: str = "sudo",
) -> None:
    if mode != "public" or not allow_remove_default_site:
        return
    subprocess.run([sudo, "rm", "-f", "/etc/nginx/sites-enabled/default"], check=False)


def apply_nginx_configuration(
    *,
    mode: str,
    port: int,
    role: str,
    certificate=None,
    https_enabled: bool,
    include_ipv6: bool,
    external_websockets: bool = True,
    destination: Path | None = None,
    site_config_path: Path | None = None,
    site_destination: Path | None = None,
    subdomain_prefixes: list[str] | None = None,
    allow_remove_default_site: bool = False,
    reload: bool = True,
    sudo: str = "sudo",
) -> ApplyResult:
    if not can_manage_nginx():
        raise NginxUnavailableError(
            "nginx configuration requires sudo privileges and nginx assets. "
            "Install nginx with 'sudo apt update && sudo apt install nginx'."
        )

    record_lock_state(mode, port, role)

    primary_dest = destination or Path("/etc/nginx/sites-enabled/arthexis.conf")
    managed_destination = site_destination or primary_dest
    try:
        config_content = generate_unified_config(
            mode,
            port,
            certificate=certificate,
            https_enabled=https_enabled,
            include_ipv6=include_ipv6,
            external_websockets=external_websockets,
            site_config_path=site_config_path,
            subdomain_prefixes=subdomain_prefixes,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    subprocess.run([sudo, "mkdir", "-p", str(SITES_ENABLED_DIR)], check=False)

    _write_config_with_sudo(managed_destination, config_content, sudo=sudo)
    _ensure_site_enabled(managed_destination, sudo=sudo)
    _disable_default_site_for_public_mode(
        mode=mode,
        allow_remove_default_site=allow_remove_default_site,
        sudo=sudo,
    )

    _ensure_maintenance_assets(sudo=sudo)

    validated = False
    reloaded = False

    if reload and ensure_nginx_in_path() and shutil.which("nginx"):
        test_result = subprocess.run([sudo, "nginx", "-t"], check=False)
        validated = test_result.returncode == 0
        if validated:
            reloaded = reload_or_start_nginx(sudo)

    changed = True
    message = "Applied nginx configuration."

    return ApplyResult(
        changed=changed,
        validated=validated,
        reloaded=reloaded,
        message=message,
    )


def restart_nginx(*, sudo: str = "sudo") -> ApplyResult:
    if not can_manage_nginx():
        raise NginxUnavailableError(
            "nginx must be installed before it can be restarted. "
            "Install nginx with 'sudo apt update && sudo apt install nginx'."
        )

    if not ensure_nginx_in_path() or not shutil.which("nginx"):
        raise NginxUnavailableError(
            "nginx executable not found. Install nginx with 'sudo apt update && sudo apt install nginx'."
        )

    validated = subprocess.run([sudo, "nginx", "-t"], check=False).returncode == 0
    reloaded = False
    if validated:
        reloaded = reload_or_start_nginx(sudo)

    return ApplyResult(changed=True, validated=validated, reloaded=reloaded, message="Restarted nginx.")
