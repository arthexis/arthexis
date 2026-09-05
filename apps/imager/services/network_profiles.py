"""NetworkManager profile discovery for imager customization."""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path

from .models import (
    DEFAULT_HOST_NETWORK_PROFILE_DIR,
    NETWORK_MANAGER_CONNECTIONS_REMOTE_PATH,
    ImagerBuildError,
    NetworkProfileInfo,
)

DEFAULT_CHARGER_NETWORK_PROFILE_ID = "arthexis-charger-eth0"
DEFAULT_CHARGER_NETWORK_PROFILE_PATH = (
    Path(__file__).resolve().parent.parent
    / "templates"
    / "network"
    / "arthexis-charger-eth0.nmconnection"
)
DEFAULT_CHARGER_NETWORK_INTERFACE = "eth0"
DEFAULT_CHARGER_NETWORK_ADDRESS = "192.168.129.10/24"
CHARGER_NETWORK_ADDRESS_ENV = "IMAGER_CHARGER_ETH0_ADDRESS"
DEFAULT_GWAY_PARENT_MANAGEMENT_ADDRESS = "192.168.129.1/24"


def charger_network_address() -> str:
    """Return the validated charger-facing IPv4 interface address for new images."""

    configured = os.environ.get(
        CHARGER_NETWORK_ADDRESS_ENV, DEFAULT_CHARGER_NETWORK_ADDRESS
    ).strip()
    try:
        interface = ipaddress.ip_interface(configured)
    except ValueError as exc:
        raise ImagerBuildError(
            f"{CHARGER_NETWORK_ADDRESS_ENV} must be an IPv4 interface address with prefix length."
        ) from exc
    if interface.version != 4:
        raise ImagerBuildError(
            f"{CHARGER_NETWORK_ADDRESS_ENV} must be an IPv4 interface address."
        )
    return str(interface)


def charger_network_host() -> str:
    """Return the host address used as the canonical wired GWAY management target."""

    return str(ipaddress.ip_interface(charger_network_address()).ip)


def _parse_network_profile_value(profile_path: Path, *, section: str, key: str) -> str:
    """Read one NetworkManager keyfile value when present."""

    try:
        lines = profile_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""

    current_section = ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip().lower()
            continue
        if current_section != section.lower() or "=" not in line:
            continue
        candidate_key, value = line.split("=", 1)
        if candidate_key.strip().lower() == key.lower():
            return value.strip()
    return ""


def _parse_network_profile_id(profile_path: Path) -> str:
    """Read a NetworkManager connection id from a keyfile profile when present."""

    return (
        _parse_network_profile_value(profile_path, section="connection", key="id")
        or profile_path.stem
    )


def _parse_network_profile_interface(profile_path: Path) -> str:
    """Read the interface pinned by a NetworkManager keyfile profile."""

    return _parse_network_profile_value(
        profile_path,
        section="connection",
        key="interface-name",
    )


def _network_profile_remote_filename(source_path: Path) -> str:
    """Return a safe NetworkManager keyfile name for image injection."""

    filename = source_path.name
    if not filename.endswith(".nmconnection"):
        filename = f"{filename}.nmconnection"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", filename)


def _render_default_charger_profile(*, output_dir: Path | None = None) -> Path:
    """Return the built-in charger profile, rendering an override when configured."""

    address = charger_network_address()
    if address == DEFAULT_CHARGER_NETWORK_ADDRESS:
        return DEFAULT_CHARGER_NETWORK_PROFILE_PATH
    if not DEFAULT_CHARGER_NETWORK_PROFILE_PATH.is_file():
        raise ImagerBuildError(
            "Built-in charger Ethernet NetworkManager profile is missing: "
            f"{DEFAULT_CHARGER_NETWORK_PROFILE_PATH}"
        )
    try:
        content = DEFAULT_CHARGER_NETWORK_PROFILE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise ImagerBuildError(
            f"Could not read built-in charger Ethernet profile: {exc}"
        ) from exc
    content = content.replace(
        f"address1={DEFAULT_CHARGER_NETWORK_ADDRESS}", f"address1={address}"
    )
    if f"address1={address}" not in content:
        raise ImagerBuildError(
            "Built-in charger Ethernet profile does not contain its expected default address."
        )
    generated_dir = (output_dir or Path("build/rpi-imager")).expanduser().resolve()
    generated_dir.mkdir(parents=True, exist_ok=True)
    generated_path = generated_dir / ".arthexis-charger-eth0.nmconnection"
    generated_path.write_text(content, encoding="utf-8")
    try:
        generated_path.chmod(0o600)
    except OSError:
        pass
    return generated_path


def select_host_network_profiles(
    *,
    profile_dir: Path | None = None,
    names: list[str] | tuple[str, ...] | None = None,
    copy_all: bool = False,
    generated_profile_dir: Path | None = None,
) -> tuple[NetworkProfileInfo, ...]:
    """Select host profiles plus the default charger-facing Ethernet profile.

    Customized images receive a no-gateway ``eth0`` profile at
    ``192.168.129.10/24`` by default so a directly attached charger can reach
    Arthexis. ``IMAGER_CHARGER_ETH0_ADDRESS`` overrides that address. Selecting a
    host profile explicitly pinned to ``eth0`` replaces the generated default.
    """

    requested_names = tuple(name.strip() for name in (names or ()) if str(name).strip())
    candidates: list[tuple[Path, str, set[str]]] = []
    if requested_names or copy_all:
        source_dir = (
            (profile_dir or Path(DEFAULT_HOST_NETWORK_PROFILE_DIR)).expanduser().resolve()
        )
        if not source_dir.is_dir():
            raise ImagerBuildError(
                f"Host NetworkManager profile directory does not exist: {source_dir}"
            )

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
                f"Host network profile '{requested_name}' was not found. Available profiles: {available or '(none)'} ."
            )
        if match not in selected:
            selected.append(match)

    selected_interfaces = {
        _parse_network_profile_interface(source_path)
        for source_path, _profile_id in selected
    }
    selected_ids = {profile_id for _source_path, profile_id in selected}
    if (
        DEFAULT_CHARGER_NETWORK_INTERFACE not in selected_interfaces
        and DEFAULT_CHARGER_NETWORK_PROFILE_ID not in selected_ids
    ):
        default_profile_path = _render_default_charger_profile(
            output_dir=generated_profile_dir
        )
        if not default_profile_path.is_file():
            raise ImagerBuildError(
                "Built-in charger Ethernet NetworkManager profile is missing: "
                f"{default_profile_path}"
            )
        selected.append((default_profile_path, DEFAULT_CHARGER_NETWORK_PROFILE_ID))

    used_filenames: set[str] = set()
    profiles: list[NetworkProfileInfo] = []
    for source_path, profile_id in selected:
        filename = _network_profile_remote_filename(source_path)
        if profile_id == DEFAULT_CHARGER_NETWORK_PROFILE_ID:
            filename = DEFAULT_CHARGER_NETWORK_PROFILE_PATH.name
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
