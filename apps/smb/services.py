"""Service helpers for SMB discovery and persistence."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from django.db import transaction

from apps.smb.models import SMBPartition, SMBServer


class SMBDiscoveryError(RuntimeError):
    """Raised when host partition discovery cannot be completed."""


@dataclass(frozen=True)
class DiscoveredPartition:
    """Normalized block-device details from lsblk discovery."""

    device: str
    filesystem: str
    size_bytes: int | None


@transaction.atomic
def configure_server(*, name: str, host: str, port: int = 445, username: str = "", password: str = "", domain: str = "") -> SMBServer:
    """Create or update an SMBServer record."""

    server, _created = SMBServer.objects.update_or_create(
        name=name,
        defaults={
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "domain": domain,
            "is_active": True,
        },
    )
    return server


@transaction.atomic
def create_partition(
    *,
    server_name: str,
    partition_name: str,
    share_name: str,
    local_path: str,
    device: str = "",
    filesystem: str = "",
    size_bytes: int | None = None,
    mount_options: str = "rw",
) -> SMBPartition:
    """Create or update an SMB partition record for a configured server."""

    server = SMBServer.objects.get(name=server_name)
    partition, _created = SMBPartition.objects.update_or_create(
        server=server,
        share_name=share_name,
        defaults={
            "name": partition_name,
            "local_path": local_path,
            "device": device,
            "filesystem": filesystem,
            "size_bytes": size_bytes,
            "mount_options": mount_options,
            "is_enabled": True,
        },
    )
    return partition


def discover_partitions() -> list[DiscoveredPartition]:
    """Discover local block devices to help map SMB storage targets."""

    try:
        completed = subprocess.run(
            ["lsblk", "--bytes", "--json", "--output", "NAME,FSTYPE,SIZE,TYPE"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SMBDiscoveryError("lsblk command is unavailable on this host") from exc
    except subprocess.CalledProcessError as exc:
        raise SMBDiscoveryError(f"lsblk failed: {exc.stderr.strip()}") from exc

    payload = json.loads(completed.stdout)

    devices: list[DiscoveredPartition] = []

    def _collect_partition_nodes(nodes: list[dict]) -> None:
        for node in nodes:
            if node.get("type") == "part":
                size_raw = node.get("size")
                size_value = int(size_raw) if str(size_raw).isdigit() else None
                devices.append(
                    DiscoveredPartition(
                        device=f"/dev/{node.get('name', '').strip()}",
                        filesystem=node.get("fstype") or "",
                        size_bytes=size_value,
                    )
                )
            children = node.get("children") or []
            if children:
                _collect_partition_nodes(children)

    _collect_partition_nodes(payload.get("blockdevices", []))
    return devices
