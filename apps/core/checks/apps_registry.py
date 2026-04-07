"""Django checks for app registry integrity."""

from importlib import import_module

from arthexis import __version__ as ARTHEXIS_VERSION
from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from config.settings.external_dbs import external_app_module


APPS_REGISTRY_ENTRY_NOT_IMPORTABLE_ID = "core.E001"
APPS_REGISTRY_UNLISTED_LOCAL_APP_ID = "core.E002"
EXTERNAL_APP_PATH_INVALID_ID = "core.E003"
EXTERNAL_APP_VERSION_RANGE_MISSING_ID = "core.E004"
EXTERNAL_APP_VERSION_RANGE_INVALID_ID = "core.E005"
EXTERNAL_APP_VERSION_UNSUPPORTED_ID = "core.E006"


def _parse_arthexis_version(version_text: str) -> Version | None:
    """Return a parsed Arthexis version from text when it is PEP 440 compatible."""

    cleaned = version_text.strip()
    if not cleaned:
        return None

    token = cleaned.split(maxsplit=1)[0]
    token = token[1:] if token.lower().startswith("v") else token
    try:
        return Version(token)
    except InvalidVersion:
        return None


def _resolve_class(import_path: str) -> type:
    """Return the class referenced by a dotted import path."""

    if not isinstance(import_path, str):
        raise ValueError(
            "External app entries must be strings using a dotted AppConfig path "
            "(for example 'pkg.apps.PluginConfig')."
        )

    if "." not in import_path:
        raise ValueError(
            "External app entries must use a dotted AppConfig path "
            "(for example 'pkg.apps.PluginConfig')."
        )

    module_path, class_name = import_path.rsplit(".", maxsplit=1)
    module = import_module(module_path)
    resolved = getattr(module, class_name, None)
    if not isinstance(resolved, type):
        raise AttributeError(
            f"'{module_path}' does not expose a class named '{class_name}'."
        )
    return resolved


def _get_base_module(app_path: str) -> str:
    """Return the module path that should be importable for a settings app entry."""

    return external_app_module(app_path)


def _get_external_app_compatibility_errors(external_apps: list[str]) -> list[Error]:
    """Validate external app paths and Arthexis version compatibility ranges."""

    errors: list[Error] = []

    current_version = _parse_arthexis_version(ARTHEXIS_VERSION)

    for app_path in external_apps:
        try:
            app_config_class = _resolve_class(app_path)
        except (AttributeError, ImportError, ValueError) as exc:
            errors.append(
                Error(
                    f"ARTHEXIS_EXTERNAL_APPS entry '{app_path}' is not importable.",
                    hint=str(exc),
                    id=EXTERNAL_APP_PATH_INVALID_ID,
                    obj=app_path,
                )
            )
            continue

        raw_compatibility = getattr(app_config_class, "arthexis_compatibility", "")
        if raw_compatibility is None:
            compatibility_range = ""
        elif isinstance(raw_compatibility, str):
            compatibility_range = raw_compatibility.strip()
        else:
            errors.append(
                Error(
                    (
                        f"External app '{app_path}' declares an invalid "
                        f"compatibility range value of type "
                        f"'{type(raw_compatibility).__name__}'."
                    ),
                    hint="Set arthexis_compatibility to a non-empty PEP 440 specifier string.",
                    id=EXTERNAL_APP_VERSION_RANGE_INVALID_ID,
                    obj=app_path,
                )
            )
            continue

        if not compatibility_range:
            errors.append(
                Error(
                    (
                        f"External app '{app_path}' must declare "
                        "'arthexis_compatibility'."
                    ),
                    hint="Set arthexis_compatibility on the plugin AppConfig class.",
                    id=EXTERNAL_APP_VERSION_RANGE_MISSING_ID,
                    obj=app_path,
                )
            )
            continue

        try:
            supported_versions = SpecifierSet(compatibility_range)
        except InvalidSpecifier as exc:
            errors.append(
                Error(
                    (
                        f"External app '{app_path}' declares an invalid "
                        f"compatibility range '{compatibility_range}'."
                    ),
                    hint=str(exc),
                    id=EXTERNAL_APP_VERSION_RANGE_INVALID_ID,
                    obj=app_path,
                )
            )
            continue

        if current_version is not None and current_version not in supported_versions:
            errors.append(
                Error(
                    (
                        f"External app '{app_path}' supports Arthexis "
                        f"'{compatibility_range}', but running version is "
                        f"'{ARTHEXIS_VERSION}'."
                    ),
                    id=EXTERNAL_APP_VERSION_UNSUPPORTED_ID,
                    obj=app_path,
                )
            )

    return errors


def get_apps_registry_configuration_errors() -> list[Error]:
    """Return app registry wiring errors for project and local app declarations."""

    errors: list[Error] = []
    project_local_apps = list(getattr(settings, "PROJECT_LOCAL_APPS", []))
    project_apps = list(getattr(settings, "PROJECT_APPS", []))
    external_apps = list(getattr(settings, "ARTHEXIS_EXTERNAL_APPS", []))
    installed_apps = list(getattr(settings, "INSTALLED_APPS", []))

    for app_path in project_local_apps:
        try:
            import_module(_get_base_module(app_path))
        except ImportError as exc:
            errors.append(
                Error(
                    f"PROJECT_LOCAL_APPS entry '{app_path}' could not be imported.",
                    hint=f"Import error: {exc}",
                    id=APPS_REGISTRY_ENTRY_NOT_IMPORTABLE_ID,
                    obj=app_path,
                )
            )

    allowed_project_apps = set(project_local_apps) | set(project_apps)
    for app_path in installed_apps:
        if not app_path.startswith("apps."):
            continue
        if app_path in allowed_project_apps:
            continue

        errors.append(
            Error(
                (
                    "INSTALLED_APPS contains unlisted local app "
                    f"'{app_path}'. Declare it in PROJECT_LOCAL_APPS or PROJECT_APPS."
                ),
                id=APPS_REGISTRY_UNLISTED_LOCAL_APP_ID,
                obj=app_path,
            )
        )

    errors.extend(_get_external_app_compatibility_errors(external_apps))

    return errors


def enforce_apps_registry_configuration() -> None:
    """Raise an ImproperlyConfigured error when app registry checks fail."""

    errors = get_apps_registry_configuration_errors()
    if not errors:
        return

    message = "\n".join(f"[{error.id}] {error.msg}" for error in errors)
    raise ImproperlyConfigured(
        "App registry configuration is invalid:\n" + message
    )


@register("core")
def check_apps_registry_configuration(app_configs=None, **kwargs):
    """Validate local app declarations are importable and explicitly listed."""

    del app_configs, kwargs
    return get_apps_registry_configuration_errors()
