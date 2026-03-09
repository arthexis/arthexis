"""Critical regression coverage for runtime dynamic import entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from django.apps import apps as django_apps
from django.conf import settings

from apps.content.classifiers import registry as classifier_registry
from config import context_processors

pytestmark = [pytest.mark.critical, pytest.mark.regression]


@dataclass(frozen=True)
class ResolutionFailure:
    """Represents one dotted-path resolution failure with diagnostics."""

    entrypoint: str
    source: str
    error: str


def _resolve_dotted_attr(path: str):
    """Resolve ``path`` in ``module.attr`` form and return the attribute."""

    module_path, _, attr_name = path.rpartition(".")
    if not module_path or not attr_name:
        raise ValueError(f"Invalid dotted path: {path}")
    module = importlib.import_module(module_path)
    return getattr(module, attr_name)


def _import_module_if_present(module_name: str) -> ModuleType | None:
    """Import ``module_name`` only when a module spec exists."""

    if importlib.util.find_spec(module_name) is None:
        return None
    return importlib.import_module(module_name)


def _format_failures(failures: list[ResolutionFailure]) -> str:
    """Render failures in a stable diagnostic format."""

    return "\n".join(
        f"- {failure.source}: {failure.entrypoint} -> {failure.error}"
        for failure in sorted(failures, key=lambda item: (item.source, item.entrypoint))
    )


def _assert_no_resolution_failures(failures: list[ResolutionFailure], context: str) -> None:
    """Fail with explicit diagnostics when any dynamic entrypoint is unresolved."""

    assert not failures, f"{context}\nUnresolved dynamic import entrypoints:\n{_format_failures(failures)}"


def _collect_module_import_failures(
    module_names: list[tuple[str, str]],
) -> list[ResolutionFailure]:
    """Collect import failures for ``(source, module_name)`` entries."""

    failures: list[ResolutionFailure] = []
    for source, module_name in module_names:
        try:
            _import_module_if_present(module_name)
        except ModuleNotFoundError as exc:
            missing_name = exc.name or ""
            if missing_name != module_name or missing_name.startswith(("apps.", "config.")):
                failures.append(
                    ResolutionFailure(
                        entrypoint=module_name,
                        source=source,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        except (ImportError, AttributeError, ValueError, TypeError) as exc:
            failures.append(
                ResolutionFailure(
                    entrypoint=module_name,
                    source=source,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return failures


def test_settings_dotted_path_references_resolve() -> None:
    """Resolve configured dotted-path references used by framework loaders."""

    failures: list[ResolutionFailure] = []

    for backend in settings.AUTHENTICATION_BACKENDS:
        try:
            _resolve_dotted_attr(backend)
        except (ImportError, AttributeError, ValueError, TypeError) as exc:
            failures.append(
                ResolutionFailure(
                    entrypoint=backend,
                    source="settings.AUTHENTICATION_BACKENDS",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    for template in settings.TEMPLATES:
        processors = template.get("OPTIONS", {}).get("context_processors", [])
        for processor in processors:
            try:
                _resolve_dotted_attr(processor)
            except (ImportError, AttributeError, ValueError, TypeError) as exc:
                failures.append(
                    ResolutionFailure(
                        entrypoint=processor,
                        source="settings.TEMPLATES[].OPTIONS.context_processors",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

    _assert_no_resolution_failures(
        failures,
        context="Framework dotted-path settings must remain importable during install/upgrade.",
    )


def test_context_processor_admin_badge_callables_resolve() -> None:
    """Resolve all allowed admin badge value query callables."""

    failures: list[ResolutionFailure] = []
    for dotted_path in sorted(context_processors.ALLOWED_ADMIN_BADGE_CALLABLE_PATHS):
        try:
            context_processors._resolve_admin_badge_callable(dotted_path)
        except (ImportError, AttributeError, ValueError, TypeError) as exc:
            failures.append(
                ResolutionFailure(
                    entrypoint=dotted_path,
                    source="config.context_processors.ALLOWED_ADMIN_BADGE_CALLABLE_PATHS",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    _assert_no_resolution_failures(
        failures,
        context="Admin badge callable whitelist entries must remain resolvable.",
    )


def test_plugin_hook_modules_import_for_installed_apps() -> None:
    """Import app plugin hook modules used by registry discovery paths."""

    module_names: list[tuple[str, str]] = []
    apps_dir = Path(getattr(settings, "APPS_DIR", Path(settings.BASE_DIR) / "apps")).resolve()

    for app_config in django_apps.get_app_configs():
        app_path = Path(app_config.path).resolve()
        try:
            app_path.relative_to(apps_dir)
        except ValueError:
            continue

        for suffix, source in (
            ("routes", "config.route_providers.autodiscovered_route_patterns"),
            ("widgets", "apps.widgets.apps.WidgetsConfig.ready"),
            ("node_features", "apps.nodes.feature_detection.NodeFeatureDetectionRegistry.discover"),
        ):
            module_name = f"{app_config.name}.{suffix}"
            if importlib.util.find_spec(module_name) is not None:
                module_names.append((source, module_name))

    failures = _collect_module_import_failures(module_names)

    _assert_no_resolution_failures(
        failures,
        context="Registry/plugin discovery modules must remain importable.",
    )


def test_content_classifier_registry_loader_resolves_known_entrypoints() -> None:
    """Exercise the classifier registry loader against known dotted callables."""

    failures: list[ResolutionFailure] = []
    for entrypoint in sorted(
        {
            "config.context_processors.site_and_node",
            *context_processors.ALLOWED_ADMIN_BADGE_CALLABLE_PATHS,
        }
    ):
        try:
            classifier_registry._resolve_entrypoint(entrypoint)
        except (ImportError, AttributeError, ValueError, TypeError) as exc:
            failures.append(
                ResolutionFailure(
                    entrypoint=entrypoint,
                    source="apps.content.classifiers.registry._resolve_entrypoint",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    _assert_no_resolution_failures(
        failures,
        context="Classifier registry dotted entrypoints must remain importable.",
    )
