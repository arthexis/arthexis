"""Django checks for app registry integrity."""

from importlib import import_module

from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured


APPS_REGISTRY_ENTRY_NOT_IMPORTABLE_ID = "core.E001"
APPS_REGISTRY_UNLISTED_LOCAL_APP_ID = "core.E002"


def _get_base_module(app_path: str) -> str:
    """Return the module path that should be importable for a settings app entry."""

    has_app_config_class = app_path.rsplit(".", maxsplit=1)[-1][:1].isupper()
    if ".apps." in app_path:
        return app_path.rsplit(".apps.", maxsplit=1)[0]
    if has_app_config_class:
        return app_path.rsplit(".", maxsplit=1)[0]
    return app_path


def get_apps_registry_configuration_errors() -> list[Error]:
    """Return app registry wiring errors for project and local app declarations."""

    errors: list[Error] = []
    project_local_apps = list(getattr(settings, "PROJECT_LOCAL_APPS", []))
    project_apps = list(getattr(settings, "PROJECT_APPS", []))
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
