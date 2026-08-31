"""Role and profile affinity helpers for PR oversight."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

KNOWN_NODE_ROLES = ("Terminal", "Satellite", "Control", "Watchtower")

ROLE_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "Terminal": ("terminal", "codex", "patchwork", "dev environment", "pr_oversee"),
    "Satellite": ("satellite", "field node", "collection", "nmcli"),
    "Control": ("control", "rfid", "charger", "gpio", "lcd", "camera"),
    "Watchtower": ("watchtower", "hosted", "public", "production", "arthexis.com"),
}

ROLE_PATH_HINTS: Mapping[str, tuple[str, ...]] = {
    "Terminal": ("apps/terminals/", "apps/repos/", "docs/development/pr-oversee"),
    "Satellite": ("apps/nmcli/", "apps/sensors/", "apps/serialbridge/"),
    "Control": ("apps/cards/", "apps/imager/", "apps/screens/", "apps/ocpp/"),
    "Watchtower": ("apps/nginx/", "apps/reports/", "apps/sites/"),
}

HARDWARE_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "audio": ("audio", "speaker", "alsa"),
    "camera": ("camera", "video"),
    "display": ("lcd", "screen", "display"),
    "gpio": ("gpio",),
    "network": ("network", "wifi", "nmcli", "ethernet"),
    "raspberry-pi": ("raspberry", "rpi", "pi 4", "gway"),
    "rfid": ("rfid", "m220", "card reader"),
    "usb": ("usb",),
}

PRIORITY_DOMAIN_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "ocpp": (
        "ocpp",
        "ocpp 1.6",
        "ocpp 2.0.1",
        "ocpp 2.1",
        "charge point protocol",
    ),
    "imager": (
        "imager",
        "image burn",
        "burn image",
        "base image",
        "gway image",
        "usb image",
    ),
}

PRIORITY_DOMAIN_PATH_HINTS: Mapping[str, tuple[str, ...]] = {
    "ocpp": ("apps/ocpp/",),
    "imager": ("apps/imager/",),
}

INSTALL_PATH_PREFIXES = (
    ".github/workflows/",
    "config/settings/",
    "docs/development/role-based-application-profiles.md",
    "install.",
    "requirements",
    "scripts/preflight-env.sh",
    "upgrade.",
    "utils/role_app_profiles.py",
)

APP_PATH_RE = re.compile(r"^apps/([^/]+)/")


def infer_work_profile(
    *,
    title: str = "",
    body: str = "",
    files: Iterable[str] = (),
) -> dict[str, Any]:
    """Infer affected roles, apps, hardware tags, and install surface."""

    title_text = str(title or "")
    body_text = str(body or "")
    file_list = tuple(str(path).strip() for path in files if str(path).strip())
    haystack = " ".join([title_text, body_text, *file_list]).casefold()
    roles: set[str] = set()
    apps: set[str] = set()
    hardware: set[str] = set()
    priority_domains: set[str] = set()
    install_paths: set[str] = set()
    reasons: list[str] = []

    for path in file_list:
        match = APP_PATH_RE.match(path)
        if match:
            apps.add(f"apps.{match.group(1)}")
        if any(path.startswith(prefix) for prefix in INSTALL_PATH_PREFIXES):
            install_paths.add(path)

    for domain, prefixes in PRIORITY_DOMAIN_PATH_HINTS.items():
        if any(path.startswith(prefix) for path in file_list for prefix in prefixes):
            priority_domains.add(domain)
            reasons.append(f"priority-domain:{domain}")

    for role, prefixes in ROLE_PATH_HINTS.items():
        if any(path.startswith(prefix) for path in file_list for prefix in prefixes):
            roles.add(role)
            reasons.append(f"path-role:{role}")

    for role, keywords in ROLE_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            roles.add(role)
            reasons.append(f"text-role:{role}")

    for tag, keywords in HARDWARE_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            hardware.add(tag)
            reasons.append(f"hardware:{tag}")

    for domain, keywords in PRIORITY_DOMAIN_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            priority_domains.add(domain)
            reasons.append(f"priority-domain:{domain}")

    if install_paths:
        reasons.append("install-path")

    return {
        "roles": sorted(roles),
        "apps": sorted(apps),
        "hardware": sorted(hardware),
        "priorityDomains": sorted(priority_domains),
        "installPaths": sorted(install_paths),
        "reasons": sorted(dict.fromkeys(reasons)),
    }


def score_node_affinity(
    profile: Mapping[str, Any],
    *,
    node_role: str,
    installed_apps: Iterable[str] = (),
    hardware_tags: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a stable node-affinity score for an inferred work profile."""

    role = node_role.strip()
    normalized_role = role.casefold()
    affected_roles = {str(item) for item in profile.get("roles") or []}
    affected_apps = {str(item) for item in profile.get("apps") or []}
    affected_hardware = {str(item) for item in profile.get("hardware") or []}
    installed = {str(item) for item in installed_apps}
    hardware = {str(item) for item in hardware_tags}

    matched_roles = sorted(
        item for item in affected_roles if item.casefold() == normalized_role
    )
    matched_apps = sorted(affected_apps & installed)
    matched_hardware = sorted(affected_hardware & hardware)
    score = 0
    reasons: list[str] = []

    if matched_roles:
        score += 60
        reasons.append("same-role")
    elif affected_roles:
        score -= 20
        reasons.append("role-mismatch")
    else:
        score += 10
        reasons.append("role-generic")

    if matched_apps:
        score += min(30, len(matched_apps) * 6)
        reasons.append("same-app-profile")
    elif affected_apps:
        reasons.append("app-profile-mismatch")

    if matched_hardware:
        score += min(20, len(matched_hardware) * 10)
        reasons.append("same-hardware")
    elif affected_hardware:
        reasons.append("hardware-unknown")

    if profile.get("installPaths"):
        score += 5
        reasons.append("install-surface")

    return {
        "score": score,
        "classification": _classification(reasons),
        "matchedRoles": matched_roles,
        "matchedApps": matched_apps,
        "matchedHardware": matched_hardware,
        "reasons": reasons,
    }


def _classification(reasons: Iterable[str]) -> str:
    reason_set = set(reasons)
    if "same-role" in reason_set:
        return "same-role"
    if "role-mismatch" in reason_set:
        return "role-mismatch"
    if "same-app-profile" in reason_set:
        return "same-app-profile"
    if "same-hardware" in reason_set:
        return "same-hardware"
    return "generic"
