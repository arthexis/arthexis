from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from django.db.models import QuerySet

from .models import FTPFolder

RIGHTS_ORDER = "elradfmwMT"


@dataclass
class FolderBinding:
    link_name: str
    target: Path
    permissions: str


@dataclass
class UserMount:
    home: Path
    bindings: list[FolderBinding]
    permissions: str


def _rights_for_level(level: str) -> str:
    try:
        return FTPFolder.Permission(level).to_ftp_rights()
    except ValueError:
        return FTPFolder.Permission.READ_ONLY.to_ftp_rights()


def _merge_permissions(*levels: Iterable[str]) -> str:
    merged = set()
    for level in levels:
        merged.update(level)
    return "".join(ch for ch in RIGHTS_ORDER if ch in merged)


def _collect_user_permissions(folder: FTPFolder) -> dict[object, str]:
    permissions: dict[object, str] = {}
    if folder.user_id and folder.user:
        permissions[folder.user] = _rights_for_level(folder.owner_permission)
    if folder.group_id and folder.group:
        for member in folder.group.user_set.all():
            merged = _merge_permissions(
                permissions.get(member, ""),
                _rights_for_level(folder.group_permission),
            )
            permissions[member] = merged
    return permissions


def build_user_mounts(
    folders: QuerySet[FTPFolder] | Iterable[FTPFolder], mount_root: Path
) -> tuple[dict[str, UserMount], list[str]]:
    """Build per-user mount directories and return the computed plan."""

    def _create_link(alias: Path, target: Path) -> None:
        try:
            alias.symlink_to(target, target_is_directory=target.is_dir())
        except OSError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) != 1314:
                raise
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(alias), str(target)], check=True
            )

    user_mounts: dict[str, UserMount] = {}
    warnings: list[str] = []
    mount_root.mkdir(parents=True, exist_ok=True)

    for folder in folders:
        if not folder.enabled:
            continue
        target = folder.resolved_path()
        if not target.exists():
            warnings.append(f"Skipping {folder.name}: target path {target} does not exist")
            continue

        user_permissions = _collect_user_permissions(folder)
        if not user_permissions:
            warnings.append(f"Skipping {folder.name}: no eligible users")
            continue

        link_name = folder.build_link_name()
        for user, rights in user_permissions.items():
            username = getattr(user, "username", None)
            if not username:
                continue
            mount = user_mounts.setdefault(
                username,
                UserMount(home=mount_root / username, bindings=[], permissions=""),
            )
            mount.bindings.append(
                FolderBinding(link_name=link_name, target=target, permissions=rights)
            )

    for username, mount in list(user_mounts.items()):
        if not mount.bindings:
            user_mounts.pop(username, None)
            continue
        if mount.home.exists():
            shutil.rmtree(mount.home)
        mount.home.mkdir(parents=True, exist_ok=True)
        rights: set[str] = set()
        for binding in mount.bindings:
            rights.update(binding.permissions)
            alias = mount.home / binding.link_name
            if alias.exists() or alias.is_symlink():
                if alias.is_dir():
                    shutil.rmtree(alias)
                else:
                    alias.unlink()
            _create_link(alias, binding.target)
        mount.permissions = _merge_permissions(rights)

    return user_mounts, warnings


__all__ = ["FolderBinding", "UserMount", "build_user_mounts"]
