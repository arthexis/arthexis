"""Inspect and write the enabled-apps lock for role application profiles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.utils import OperationalError, ProgrammingError

from config.settings.apps import (
    _BASELINE_APP_ENTRIES,
    _BUILT_IN_APP_ENTRIES,
    _normalize_selected_app_entries,
    _split_env_lists,
    _split_setting_list,
)
from utils.enabled_apps_lock import get_enabled_apps_lock_path, write_enabled_apps_lock
from utils.role_app_profiles import (
    REQUIRED_APP_SELECTORS,
    RETIRED_RUNTIME_APP_SELECTORS,
    ResolvedAppSet,
    close_app_dependencies,
    explain_role_app_selectors,
    get_direct_lock_app_selectors,
    get_direct_lock_app_sources,
    normalize_feature_pack_name,
)

APPLICATION_TABLE = "pages_application"
PRESERVED_REQUIRED_DISABLE_BLOCKERS = frozenset({"apps.app"})
PRESERVED_REQUIRED_DEPENDENCY_DISABLES = frozenset()


def _dedupe(entries: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        normalized = entry.strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return tuple(deduped)


def _split_option_entries(values: list[str] | None) -> tuple[str, ...]:
    entries: list[str] = []
    for value in values or []:
        entries.extend(_split_setting_list(value))
    return _dedupe(entries)


def _is_retired_app_selector(entry: str) -> bool:
    selected = entry.strip()
    for selector in RETIRED_RUNTIME_APP_SELECTORS:
        label = selector.rsplit(".", 1)[-1]
        if selected in {selector, label, label.replace("_", "-")}:
            return True
    return False


def _table_columns(using: str, table_name: str) -> set[str]:
    connection = connections[using]
    with connection.cursor() as cursor:
        return {
            column.name
            for column in connection.introspection.get_table_description(
                cursor,
                table_name,
            )
        }


def _normalize_known_app_entries(
    entries: tuple[str, ...], *, option_name: str
) -> tuple[str, ...]:
    normalized_entries: list[str] = []
    unknown_entries: list[str] = []
    known_entries = set(_BUILT_IN_APP_ENTRIES)
    for entry in entries:
        if _is_retired_app_selector(entry):
            continue
        normalized = _normalize_selected_app_entries((entry,), _BUILT_IN_APP_ENTRIES)
        if normalized and all(item in known_entries for item in normalized):
            normalized_entries.extend(normalized)
        else:
            unknown_entries.append(entry)

    if unknown_entries:
        unknown = ", ".join(unknown_entries)
        raise CommandError(f"Unknown {option_name} app selector(s): {unknown}")

    return tuple(_dedupe(normalized_entries))


def _reject_cli_baseline_disables(disabled_apps: tuple[str, ...]) -> None:
    baseline_entries = set(_BASELINE_APP_ENTRIES)
    baseline_disabled = tuple(
        app_entry for app_entry in disabled_apps if app_entry in baseline_entries
    )
    if not baseline_disabled:
        return

    disabled = ", ".join(baseline_disabled)
    raise CommandError(
        "Baseline --disable app selector(s) cannot be persisted in "
        f"enabled-apps lock: {disabled}. Use ARTHEXIS_ROLE_APP_DISABLED_APPS "
        "or ARTHEXIS_DISABLED_APPS when a baseline app must be disabled at boot."
    )


def _application_disabled_entries(using: str = "default") -> tuple[str, ...]:
    connection = connections[using]
    try:
        existing_tables = set(connection.introspection.table_names())
    except (OperationalError, ProgrammingError):
        return ()

    if APPLICATION_TABLE not in existing_tables:
        return ()

    try:
        has_is_deleted = "is_deleted" in _table_columns(using, APPLICATION_TABLE)
        with connection.cursor() as cursor:
            if has_is_deleted:
                cursor.execute(
                    "SELECT name FROM pages_application "
                    "WHERE enabled = %s AND is_deleted = %s",
                    [False, False],
                )
            else:
                cursor.execute(
                    "SELECT name FROM pages_application WHERE enabled = %s",
                    [False],
                )
            return _dedupe([row[0] for row in cursor.fetchall()])
    except (OperationalError, ProgrammingError):
        return ()


def _normalize_application_disabled_entries(
    entries: tuple[str, ...],
) -> tuple[str, ...]:
    normalized_entries: list[str] = []
    for entry in entries:
        try:
            normalized_entries.extend(
                _normalize_known_app_entries(
                    (entry,), option_name="Application.enabled"
                )
            )
        except CommandError:
            continue
    return _dedupe(normalized_entries)


def _filter_preserved_application_disabled_entries(
    entries: tuple[str, ...],
) -> tuple[str, ...]:
    required_dependency_selectors = set(close_app_dependencies(REQUIRED_APP_SELECTORS))
    return _dedupe(
        [
            entry
            for entry in entries
            if entry not in required_dependency_selectors
            or entry in PRESERVED_REQUIRED_DISABLE_BLOCKERS
            or entry in PRESERVED_REQUIRED_DEPENDENCY_DISABLES
        ]
    )


def _apply_preserved_application_disables(
    result: ResolvedAppSet,
    preserved_application_disabled_apps: tuple[str, ...],
) -> ResolvedAppSet:
    if not preserved_application_disabled_apps:
        return result

    preserved_disabled = set(preserved_application_disabled_apps)
    selectors = tuple(
        selector for selector in result.selectors if selector not in preserved_disabled
    )
    explanations = tuple(
        item for item in result.explanations if item.selector not in preserved_disabled
    )
    return ResolvedAppSet(
        role=result.role,
        role_profile=result.role_profile,
        selectors=selectors,
        explanations=explanations,
        fallback_reason=result.fallback_reason,
    )


def _direct_result_selectors(result: ResolvedAppSet) -> tuple[str, ...]:
    return get_direct_lock_app_selectors(result)


class Command(BaseCommand):
    """Render, explain, and optionally write ``.locks/enabled_apps.lck``."""

    help = (
        "Resolve enabled Django apps from a node role, feature packs, explicit "
        "includes, disables, and manifest dependency closure. Use --write to "
        "persist .locks/enabled_apps.lck."
    )

    def add_arguments(self, parser) -> None:
        """Register command flags."""

        parser.add_argument(
            "--role",
            default=getattr(settings, "NODE_ROLE", "Terminal"),
            help="Node role profile to resolve. Defaults to settings.NODE_ROLE.",
        )
        parser.add_argument(
            "--feature-pack",
            action="append",
            default=[],
            help=(
                "Feature pack to include. May be repeated and accepts comma, "
                "semicolon, or whitespace separated values."
            ),
        )
        parser.add_argument(
            "--include",
            action="append",
            default=[],
            help=(
                "Explicit app selector or label to include. May be repeated and "
                "accepts comma, semicolon, or whitespace separated values."
            ),
        )
        parser.add_argument(
            "--disable",
            action="append",
            default=[],
            help=(
                "App selector or label to exclude from the resolved set. May be "
                "repeated and accepts comma, semicolon, or whitespace separated values."
            ),
        )
        parser.add_argument(
            "--base-dir",
            type=Path,
            default=Path(settings.BASE_DIR),
            help="Repository root where .locks/enabled_apps.lck is written.",
        )
        parser.add_argument(
            "--write",
            action="store_true",
            help="Write the resolved selector set to .locks/enabled_apps.lck.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit a machine-readable JSON document.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Fail for unknown roles instead of using the full-app fallback.",
        )
        parser.add_argument(
            "--preserve-application-disables",
            action="store_true",
            help=(
                "Add currently disabled Application rows to the disabled selector "
                "set before resolving the lock."
            ),
        )

    def handle(self, *args, **options) -> None:
        """Resolve the app set, render it, and optionally persist the lock."""

        role = str(options["role"]).strip()
        base_dir = Path(options["base_dir"])
        feature_packs = _dedupe(
            [
                *_split_env_lists(
                    "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
                    "ARTHEXIS_FEATURE_PACKS",
                ),
                *_split_option_entries(options["feature_pack"]),
            ]
        )
        env_disabled_apps = _normalize_known_app_entries(
            _split_env_lists(
                "ARTHEXIS_ROLE_APP_DISABLED_APPS",
                "ARTHEXIS_DISABLED_APPS",
            ),
            option_name="--disable",
        )
        preserved_application_disabled_apps = (
            _filter_preserved_application_disabled_entries(
                _normalize_application_disabled_entries(_application_disabled_entries())
            )
            if options["preserve_application_disables"]
            else ()
        )
        option_disabled_apps = _normalize_known_app_entries(
            _split_option_entries(options["disable"]),
            option_name="--disable",
        )
        _reject_cli_baseline_disables(option_disabled_apps)
        preserved_resolution_disabled_apps = tuple(
            entry
            for entry in preserved_application_disabled_apps
            if entry not in PRESERVED_REQUIRED_DEPENDENCY_DISABLES
        )
        disabled_apps = _dedupe(
            [
                *env_disabled_apps,
                *preserved_resolution_disabled_apps,
                *option_disabled_apps,
            ]
        )
        reported_disabled_apps = _dedupe(
            [
                *env_disabled_apps,
                *preserved_application_disabled_apps,
                *option_disabled_apps,
            ]
        )
        explicit_apps = _normalize_known_app_entries(
            _split_option_entries(options["include"]),
            option_name="--include",
        )
        try:
            feature_packs = tuple(
                normalize_feature_pack_name(feature_pack)
                for feature_pack in feature_packs
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        fallback_app_selectors = None if options["strict"] else _BUILT_IN_APP_ENTRIES

        try:
            result = explain_role_app_selectors(
                role,
                feature_packs=feature_packs,
                explicit_apps=explicit_apps,
                disabled_apps=disabled_apps,
                fallback_app_selectors=fallback_app_selectors,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        result = _apply_preserved_application_disables(
            result,
            preserved_application_disabled_apps,
        )

        lock_path = get_enabled_apps_lock_path(base_dir)
        written = False
        if options["write"]:
            lock_path = write_enabled_apps_lock(
                result.selectors,
                base_dir,
                direct_apps=_direct_result_selectors(result),
                direct_app_sources=get_direct_lock_app_sources(result),
            )
            written = True

        if options["json"]:
            self.stdout.write(
                json.dumps(
                    self._json_payload(
                        result,
                        lock_path=lock_path,
                        feature_packs=feature_packs,
                        explicit_apps=explicit_apps,
                        disabled_apps=reported_disabled_apps,
                        preserved_application_disabled_apps=preserved_application_disabled_apps,
                        written=written,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            return

        self._write_text(
            result,
            lock_path=lock_path,
            feature_packs=feature_packs,
            explicit_apps=explicit_apps,
            disabled_apps=reported_disabled_apps,
            preserved_application_disabled_apps=preserved_application_disabled_apps,
            written=written,
        )

    def _json_payload(
        self,
        result: ResolvedAppSet,
        *,
        lock_path: Path,
        feature_packs: tuple[str, ...],
        explicit_apps: tuple[str, ...],
        disabled_apps: tuple[str, ...],
        preserved_application_disabled_apps: tuple[str, ...],
        written: bool,
    ) -> dict[str, Any]:
        return {
            "role": result.role,
            "roleProfile": result.role_profile.value if result.role_profile else None,
            "fallbackReason": result.fallback_reason,
            "featurePacks": list(feature_packs),
            "explicitApps": list(explicit_apps),
            "disabledApps": list(disabled_apps),
            "preservedApplicationDisabledApps": list(
                preserved_application_disabled_apps
            ),
            "lockPath": str(lock_path),
            "written": written,
            "enabledAppCount": len(result.selectors),
            "enabledApps": [
                {"selector": item.selector, "reasons": list(item.reasons)}
                for item in result.explanations
            ],
            "destructiveCleanup": (
                "disabled app tables are left in place; cleanup requires a separate "
                "explicit destructive operator action"
            ),
        }

    def _write_text(
        self,
        result: ResolvedAppSet,
        *,
        lock_path: Path,
        feature_packs: tuple[str, ...],
        explicit_apps: tuple[str, ...],
        disabled_apps: tuple[str, ...],
        preserved_application_disabled_apps: tuple[str, ...],
        written: bool,
    ) -> None:
        role_profile = result.role_profile.value if result.role_profile else "fallback"
        self.stdout.write(f"role={result.role}")
        self.stdout.write(f"role_profile={role_profile}")
        if result.fallback_reason:
            self.stdout.write(f"fallback_reason={result.fallback_reason}")
        self.stdout.write(
            "feature_packs=" + (",".join(feature_packs) if feature_packs else "(none)")
        )
        self.stdout.write(
            "explicit_apps=" + (",".join(explicit_apps) if explicit_apps else "(none)")
        )
        self.stdout.write(
            "disabled_apps=" + (",".join(disabled_apps) if disabled_apps else "(none)")
        )
        self.stdout.write(
            "preserved_application_disabled_apps="
            + (
                ",".join(preserved_application_disabled_apps)
                if preserved_application_disabled_apps
                else "(none)"
            )
        )
        self.stdout.write(f"lock_path={lock_path}")
        self.stdout.write(f"written={'yes' if written else 'no'}")
        self.stdout.write(f"enabled_app_count={len(result.selectors)}")
        self.stdout.write(
            "destructive_cleanup=disabled app tables are left in place; cleanup "
            "requires a separate explicit destructive operator action"
        )
        self.stdout.write("")
        for item in result.explanations:
            self.stdout.write(f"{item.selector}\t{', '.join(item.reasons)}")
