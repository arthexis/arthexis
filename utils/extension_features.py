"""Synchronize Suite Feature definitions contributed by extension manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:  # pragma: no cover - Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from utils.extensions import ExtensionError, load_extension_manifests


@dataclass(frozen=True)
class ExtensionSuiteFeature:
    """One Suite Feature definition owned by an installed extension."""

    extension_name: str
    extension_repository: str
    slug: str
    display: str
    main_app: str
    summary: str = ""
    enabled_by_default: bool = False


def _load_manifest_payload(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ExtensionError(f"Unable to read extension metadata from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExtensionError(f"{path}: expected a TOML table.")
    return payload


def _parse_suite_feature(
    value: object,
    *,
    extension_name: str,
    extension_repository: str,
    path: Path,
) -> ExtensionSuiteFeature:
    if not isinstance(value, dict):
        raise ExtensionError(f"{path}: each [[suite_features]] entry must be a table.")

    slug = str(value.get("slug") or "").strip()
    display = str(value.get("display") or "").strip()
    main_app = str(value.get("main_app") or "").strip()
    summary = str(value.get("summary") or "").strip()
    enabled_by_default = value.get("enabled_by_default", False)

    if not slug:
        raise ExtensionError(f"{path}: suite_features.slug is required.")
    if not display:
        raise ExtensionError(f"{path}: suite_features.display is required for {slug!r}.")
    if not main_app:
        raise ExtensionError(f"{path}: suite_features.main_app is required for {slug!r}.")
    if not isinstance(enabled_by_default, bool):
        raise ExtensionError(
            f"{path}: suite_features.enabled_by_default must be true or false."
        )

    return ExtensionSuiteFeature(
        extension_name=extension_name,
        extension_repository=extension_repository,
        slug=slug,
        display=display,
        main_app=main_app,
        summary=summary,
        enabled_by_default=enabled_by_default,
    )


def load_extension_suite_features(
    base_dir: str | Path | None = None,
) -> tuple[ExtensionSuiteFeature, ...]:
    """Return structured Suite Feature definitions from installed extensions."""

    definitions: list[ExtensionSuiteFeature] = []
    owners: dict[str, str] = {}
    for manifest in load_extension_manifests(base_dir):
        payload = _load_manifest_payload(manifest.path)
        raw_features = payload.get("suite_features", [])
        if not isinstance(raw_features, list):
            raise ExtensionError(
                f"{manifest.path}: suite_features must be an array of tables."
            )
        for raw_feature in raw_features:
            definition = _parse_suite_feature(
                raw_feature,
                extension_name=manifest.name,
                extension_repository=manifest.repository,
                path=manifest.path,
            )
            previous_owner = owners.setdefault(definition.slug, manifest.name)
            if previous_owner != manifest.name:
                raise ExtensionError(
                    f"Suite Feature {definition.slug!r} is declared by both "
                    f"{previous_owner!r} and {manifest.name!r}."
                )
            definitions.append(definition)
    return tuple(definitions)


def sync_extension_suite_features(
    base_dir: str | Path | None = None,
) -> tuple[int, int]:
    """Create/update extension-owned Suite Features without changing runtime state."""

    from apps.app.models import Application
    from apps.features.models import Feature

    created_count = 0
    updated_count = 0
    for definition in load_extension_suite_features(base_dir):
        application, _ = Application.objects.get_or_create(name=definition.main_app)
        feature = Feature.objects.filter(slug=definition.slug).first()
        ownership = {
            "extension": definition.extension_name,
            "extension_repository": definition.extension_repository,
        }

        if feature is None:
            Feature.objects.create(
                slug=definition.slug,
                display=definition.display,
                source=Feature.Source.CUSTOM,
                summary=definition.summary,
                is_enabled=definition.enabled_by_default,
                main_app=application,
                metadata=ownership,
            )
            created_count += 1
            continue

        existing_owner = (feature.metadata or {}).get("extension")
        if existing_owner and existing_owner != definition.extension_name:
            raise ExtensionError(
                f"Suite Feature {definition.slug!r} is already owned by extension "
                f"{existing_owner!r}."
            )
        if feature.source == Feature.Source.MAINSTREAM and not existing_owner:
            raise ExtensionError(
                f"Suite Feature {definition.slug!r} is a mainstream feature and cannot "
                "be replaced by an extension definition."
            )

        metadata = dict(feature.metadata or {})
        metadata.update(ownership)
        changed_fields: list[str] = []
        for field_name, value in (
            ("display", definition.display),
            ("summary", definition.summary),
            ("main_app", application),
            ("metadata", metadata),
        ):
            if getattr(feature, field_name) != value:
                setattr(feature, field_name, value)
                changed_fields.append(field_name)

        if feature.source != Feature.Source.CUSTOM:
            feature.source = Feature.Source.CUSTOM
            changed_fields.append("source")

        if changed_fields:
            feature.save(update_fields=[*changed_fields, "updated_at"])
            updated_count += 1

    return created_count, updated_count
