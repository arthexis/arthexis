from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from apps.nginx.config_utils import slugify
from apps.nginx.renderers import apply_site_entries, generate_primary_config

SITES_AVAILABLE_DIR = Path("/etc/nginx/sites-available")
SITES_ENABLED_DIR = Path("/etc/nginx/sites-enabled")


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


@dataclass
class SecondaryInstance:
    name: str
    path: Path
    port: int
    role: str = "Terminal"

    @property
    def lock_dir(self) -> Path:
        return self.path / ".locks"


class SecondaryInstanceError(Exception):
    """Raised when a referenced sibling installation cannot be used."""


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


def _read_secondary_port(lock_dir: Path) -> int | None:
    port_path = lock_dir / "backend_port.lck"
    try:
        value = port_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    try:
        parsed = int(value)
    except ValueError:
        return None

    if parsed < 1 or parsed > 65535:
        return None
    return parsed


def _read_secondary_role(lock_dir: Path) -> str:
    role_path = lock_dir / "role.lck"
    try:
        value = role_path.read_text(encoding="utf-8").strip()
    except OSError:
        return "Terminal"
    return value or "Terminal"


def discover_secondary_instances(base_dir: Path | str | None = None) -> list[SecondaryInstance]:
    base = Path(base_dir or settings.BASE_DIR).resolve()
    parent = base.parent
    instances: list[SecondaryInstance] = []

    for sibling in parent.iterdir():
        if not sibling.is_dir():
            continue
        if sibling.resolve() == base:
            continue
        lock_dir = sibling / ".locks"
        port = _read_secondary_port(lock_dir)
        if port is None:
            continue
        role = _read_secondary_role(lock_dir)
        instances.append(
            SecondaryInstance(
                name=sibling.name,
                path=sibling.resolve(),
                port=port,
                role=role,
            )
        )

    return sorted(instances, key=lambda instance: instance.name)


def get_secondary_instance(name: str, base_dir: Path | str | None = None) -> SecondaryInstance:
    if not name:
        raise SecondaryInstanceError("Secondary instance name is required.")

    instances = discover_secondary_instances(base_dir)
    for instance in instances:
        if instance.name == name:
            return instance

    raise SecondaryInstanceError(
        f"Secondary instance '{name}' not found. Ensure sibling installs include .locks/backend_port.lck."
    )


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
    certificate=None,
    https_enabled: bool,
    include_ipv6: bool,
    external_websockets: bool = True,
    destination: Path | None = None,
    site_config_path: Path | None = None,
    site_destination: Path | None = None,
    subdomain_prefixes: list[str] | None = None,
    reload: bool = True,
    secondary_instance: SecondaryInstance | None = None,
    sudo: str = "sudo",
) -> ApplyResult:
    if not can_manage_nginx():
        raise NginxUnavailableError(
            "nginx configuration requires sudo privileges and nginx assets. "
            "Install nginx with 'sudo apt-get update && sudo apt-get install nginx'."
        )

    record_lock_state(mode, port, role)

    if secondary_instance:
        if secondary_instance.path.parent.resolve() != Path(settings.BASE_DIR).resolve().parent:
            raise ValidationError("Secondary instance must be a sibling directory to the current install.")
        if secondary_instance.port == port:
            raise ValidationError("Secondary instance must use a different backend port for failover.")
        if not secondary_instance.path.exists():
            raise ValidationError("Secondary instance directory does not exist on disk.")

    proxy_target = None
    if secondary_instance:
        proxy_target = f"arthexis-{slugify(secondary_instance.name)}-pool"

    subprocess.run([sudo, "mkdir", "-p", str(SITES_ENABLED_DIR)], check=False)
    subprocess.run([sudo, "sh", "-c", "rm -f /etc/nginx/sites-enabled/arthexis*.conf"], check=False)
    subprocess.run([sudo, "sh", "-c", "rm -f /etc/nginx/sites-available/default"], check=False)
    subprocess.run([sudo, "sh", "-c", "rm -f /etc/nginx/conf.d/arthexis-*.conf"], check=False)

    primary_dest = destination or Path("/etc/nginx/sites-enabled/arthexis.conf")
    config_content = generate_primary_config(
        mode,
        port,
        certificate=certificate,
        https_enabled=https_enabled,
        include_ipv6=include_ipv6,
        external_websockets=external_websockets,
        secondary_instance=secondary_instance,
    )
    _write_config_with_sudo(primary_dest, config_content, sudo=sudo)
    _ensure_site_enabled(primary_dest, sudo=sudo)

    site_changed = False
    if site_config_path and site_destination:
        try:
            site_changed = apply_site_entries(
                site_config_path,
                mode,
                port,
                site_destination,
                https_enabled=https_enabled,
                external_websockets=external_websockets,
                proxy_target=proxy_target,
                subdomain_prefixes=subdomain_prefixes,
                sudo=sudo,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        else:
            _ensure_site_enabled(site_destination, sudo=sudo)

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
