"""NetworkManager profile discovery for imager customization."""

from __future__ import annotations

import re
from pathlib import Path

from .models import (
    DEFAULT_HOST_NETWORK_PROFILE_DIR,
    NETWORK_MANAGER_CONNECTIONS_REMOTE_PATH,
    ImagerBuildError,
    NetworkProfileInfo,
)


def _parse_network_profile_id(profile_path: Path) -> str:
    """Read a NetworkManager connection id from a keyfile profile when present."""

    try:
        lines = profile_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return profile_path.stem

    in_connection_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_connection_section = line.lower() == "[connection]"
            continue
        if not in_connection_section or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() == "id" and value.strip():
            return value.strip()
    return profile_path.stem


def _network_profile_remote_filename(source_path: Path) -> str:
    """Return a safe NetworkManager keyfile name for image injection."""

    filename = source_path.name
    if not filename.endswith(".nmconnection"):
        filename = f"{filename}.nmconnection"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", filename)


def select_host_network_profiles(
    *,
    profile_dir: Path | None = None,
    names: list[str] | tuple[str, ...] | None = None,
    copy_all: bool = False,
) -> tuple[NetworkProfileInfo, ...]:
    """Select host NetworkManager profiles for copying into the generated image."""

    requested_names = tuple(name.strip() for name in (names or ()) if str(name).strip())
    if not requested_names and not copy_all:
        return ()

    source_dir = (
        (profile_dir or Path(DEFAULT_HOST_NETWORK_PROFILE_DIR)).expanduser().resolve()
    )
    if not source_dir.is_dir():
        raise ImagerBuildError(
            f"Host NetworkManager profile directory does not exist: {source_dir}"
        )

    candidates: list[tuple[Path, str, set[str]]] = []
    for path in sorted(source_dir.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or path.is_symlink() or not path.is_file():
            continue
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(source_dir):
            continue
        profile_id = _parse_network_profile_id(path)
        candidates.append(
            (
                path,
                profile_id,
                {path.name, path.stem, profile_id},
            )
        )

    selected: list[tuple[Path, str]] = []
    if copy_all:
        selected.extend((path, profile_id) for path, profile_id, _aliases in candidates)

    for requested_name in requested_names:
        match = next(
            (
                (path, profile_id)
                for path, profile_id, aliases in candidates
                if requested_name in aliases
            ),
            None,
        )
        if match is None:
            available = ", ".join(
                sorted({alias for _, _, aliases in candidates for alias in aliases})
            )
            raise ImagerBuildError(
                f"Host network profile '{requested_name}' was not found. Available profiles: {available or '(none)'}."
            )
        if match not in selected:
            selected.append(match)

    used_filenames: set[str] = set()
    profiles: list[NetworkProfileInfo] = []
    for source_path, profile_id in selected:
        filename = _network_profile_remote_filename(source_path)
        if filename in used_filenames:
            stem = Path(filename).stem
            suffix = Path(filename).suffix or ".nmconnection"
            counter = 2
            while f"{stem}-{counter}{suffix}" in used_filenames:
                counter += 1
            filename = f"{stem}-{counter}{suffix}"
        used_filenames.add(filename)
        profiles.append(
            NetworkProfileInfo(
                name=profile_id,
                filename=filename,
                source_path=source_path,
                remote_path=f"{NETWORK_MANAGER_CONNECTIONS_REMOTE_PATH}/{filename}",
            )
        )
    return tuple(profiles)
