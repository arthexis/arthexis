"""Helpers for external-app SQLite database naming."""

import re


def external_app_module(external_app_path: str) -> str:
    """Return import module path for an external app settings entry."""

    if ".apps." in external_app_path:
        return external_app_path.rsplit(".apps.", maxsplit=1)[0]

    token = external_app_path.rsplit(".", maxsplit=1)[-1]
    if token[:1].isupper() and "." in external_app_path:
        return external_app_path.rsplit(".", maxsplit=1)[0]

    return external_app_path


def external_app_database_alias_mapping(external_apps: list[str]) -> dict[str, str]:
    """Return a collision-safe mapping of module roots to database aliases."""

    mapping: dict[str, str] = {}
    seen_aliases: set[str] = set()
    for index, app_path in enumerate(external_apps, start=1):
        module_root = external_app_module(app_path)
        alias = external_app_database_alias(app_path, fallback_index=index)
        base_alias = alias
        suffix_index = 2
        while alias in seen_aliases:
            alias = f"{base_alias}_{suffix_index}"
            suffix_index += 1
        seen_aliases.add(alias)
        mapping[module_root] = alias

    return mapping


def external_app_database_alias(external_app_path: str, *, fallback_index: int) -> str:
    """Return deterministic database alias for an external app."""

    module_path = external_app_module(external_app_path)
    suffix = module_path.rsplit(".", maxsplit=1)[-1].strip().lower()
    suffix = re.sub(r"[^a-z0-9]+", "_", suffix).strip("_")
    if not suffix:
        suffix = f"app_{fallback_index}"
    return f"external_{suffix}"
