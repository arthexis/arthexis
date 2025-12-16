from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from apps.nginx.maintenance import refresh_maintenance
from apps.nginx.renderers import apply_site_entries, generate_primary_config


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
            "Install nginx with 'sudo apt-get update && sudo apt-get install nginx'."
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

    subprocess.run([sudo, "mkdir", "-p", str(dest.parent)], check=True)
    subprocess.run([sudo, "cp", str(temp_path), str(dest)], check=True)
    temp_path.unlink(missing_ok=True)


def apply_nginx_configuration(
    *,
    mode: str,
    port: int,
    role: str,
    include_ipv6: bool,
    destination: Path | None = None,
    site_config_path: Path | None = None,
    site_destination: Path | None = None,
    reload: bool = True,
    sudo: str = "sudo",
) -> ApplyResult:
    if not can_manage_nginx():
        raise NginxUnavailableError(
            "nginx configuration requires sudo privileges and nginx assets. "
            "Install nginx with 'sudo apt-get update && sudo apt-get install nginx'."
        )

    base_dir = Path(settings.BASE_DIR)
    record_lock_state(mode, port, role)

    subprocess.run([sudo, "mkdir", "-p", "/etc/nginx/sites-enabled"], check=False)
    subprocess.run([sudo, "sh", "-c", "rm -f /etc/nginx/sites-enabled/arthexis*.conf"], check=False)
    subprocess.run([sudo, "sh", "-c", "rm -f /etc/nginx/sites-available/default"], check=False)
    subprocess.run([sudo, "sh", "-c", "rm -f /etc/nginx/conf.d/arthexis-*.conf"], check=False)

    primary_dest = destination or Path("/etc/nginx/sites-enabled/arthexis.conf")
    config_content = generate_primary_config(mode, port, include_ipv6=include_ipv6)
    _write_config_with_sudo(primary_dest, config_content, sudo=sudo)

    site_changed = False
    if site_config_path and site_destination:
        try:
            site_changed = apply_site_entries(site_config_path, mode, port, site_destination)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    maintenance_updated = refresh_maintenance(base_dir, [primary_dest], sudo=sudo)

    validated = False
    reloaded = False

    if reload and ensure_nginx_in_path() and shutil.which("nginx"):
        test_result = subprocess.run([sudo, "nginx", "-t"], check=False)
        validated = test_result.returncode == 0
        if validated:
            reloaded = reload_or_start_nginx(sudo)

    changed = True or site_changed or maintenance_updated
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
            "Install nginx with 'sudo apt-get update && sudo apt-get install nginx'."
        )

    if not ensure_nginx_in_path() or not shutil.which("nginx"):
        raise NginxUnavailableError(
            "nginx executable not found. Install nginx with 'sudo apt-get update && sudo apt-get install nginx'."
        )

    validated = subprocess.run([sudo, "nginx", "-t"], check=False).returncode == 0
    reloaded = False
    if validated:
        reloaded = reload_or_start_nginx(sudo)

    return ApplyResult(changed=True, validated=validated, reloaded=reloaded, message="Restarted nginx.")
