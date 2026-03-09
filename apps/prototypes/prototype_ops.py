"""Helpers for scaffolded prototype apps and activation state."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import re
import subprocess

from django.conf import settings

from apps.prototypes.models import Prototype


ACTIVE_PROTOTYPE_LOCK = "active_prototype.lck"
PREVIOUS_BACKEND_PORT_LOCK = "prototype_previous_backend_port.lck"
PROTOTYPE_ENV_START = "# BEGIN ARTHEXIS PROTOTYPE"
PROTOTYPE_ENV_END = "# END ARTHEXIS PROTOTYPE"
_PROTOTYPE_BLOCK_RE = re.compile(
    rf"\n?{re.escape(PROTOTYPE_ENV_START)}\n.*?{re.escape(PROTOTYPE_ENV_END)}\n?",
    re.DOTALL,
)


def _base_dir(base_dir: Path | None = None) -> Path:
    return Path(base_dir or settings.BASE_DIR)


def _apps_dir(base_dir: Path | None = None) -> Path:
    return Path(getattr(settings, "APPS_DIR", _base_dir(base_dir) / "apps"))


def active_prototype_lock_path(base_dir: Path | None = None) -> Path:
    return _base_dir(base_dir) / ".locks" / ACTIVE_PROTOTYPE_LOCK


def backend_port_lock_path(base_dir: Path | None = None) -> Path:
    return _base_dir(base_dir) / ".locks" / "backend_port.lck"


def previous_backend_port_lock_path(base_dir: Path | None = None) -> Path:
    return _base_dir(base_dir) / ".locks" / PREVIOUS_BACKEND_PORT_LOCK


def env_path(base_dir: Path | None = None) -> Path:
    return _base_dir(base_dir) / "arthexis.env"


def _format_env_value(value: str) -> str:
    if value == "":
        return '""'
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
        .replace("!", "\\!")
    )
    return f'"{escaped}"'


def _render_managed_env_block(values: OrderedDict[str, str]) -> str:
    lines = [PROTOTYPE_ENV_START]
    lines.extend(f"{key}={_format_env_value(value)}" for key, value in values.items())
    lines.append(PROTOTYPE_ENV_END)
    return "\n".join(lines) + "\n"


def _rewrite_managed_env_block(path: Path, values: OrderedDict[str, str]) -> None:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    stripped = _PROTOTYPE_BLOCK_RE.sub("\n", current).strip()
    if not values:
        if stripped:
            path.write_text(stripped + "\n", encoding="utf-8")
        elif path.exists():
            path.unlink()
        return

    block = _render_managed_env_block(values).strip()
    next_text = f"{stripped}\n\n{block}".strip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(next_text, encoding="utf-8")


def build_prototype_env_for_base(
    prototype: Prototype, *, base_dir: Path | None = None
) -> OrderedDict[str, str]:
    resolved_base_dir = _base_dir(base_dir)
    values: OrderedDict[str, str] = OrderedDict()
    values["ARTHEXIS_ACTIVE_PROTOTYPE"] = prototype.slug
    values["ARTHEXIS_PROTOTYPE_APP"] = prototype.scaffold_module
    values["ARTHEXIS_SQLITE_PATH"] = str(
        prototype.resolved_sqlite_path(base_dir=resolved_base_dir)
    )
    values["ARTHEXIS_SQLITE_TEST_PATH"] = str(
        prototype.resolved_sqlite_test_path(base_dir=resolved_base_dir)
    )
    values["DJANGO_CACHE_DIR"] = str(
        prototype.resolved_cache_dir(base_dir=resolved_base_dir)
    )
    for key in sorted(prototype.env_overrides):
        values[key] = prototype.env_overrides[key]
    return values


def activate_prototype(prototype: Prototype, *, base_dir: Path | None = None) -> Prototype:
    """Persist activation state for *prototype* without restarting the suite."""

    resolved_base_dir = _base_dir(base_dir)
    prototype.resolved_sqlite_path(base_dir=resolved_base_dir).parent.mkdir(
        parents=True, exist_ok=True
    )
    prototype.resolved_sqlite_test_path(base_dir=resolved_base_dir).parent.mkdir(
        parents=True, exist_ok=True
    )
    prototype.resolved_cache_dir(base_dir=resolved_base_dir).mkdir(
        parents=True,
        exist_ok=True,
    )

    _rewrite_managed_env_block(
        env_path(resolved_base_dir),
        build_prototype_env_for_base(prototype, base_dir=resolved_base_dir),
    )

    lock_path = active_prototype_lock_path(resolved_base_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(prototype.slug + "\n", encoding="utf-8")

    port_lock = backend_port_lock_path(resolved_base_dir)
    previous_port_lock = previous_backend_port_lock_path(resolved_base_dir)
    port_lock.parent.mkdir(parents=True, exist_ok=True)
    if not previous_port_lock.exists():
        current_port = port_lock.read_text(encoding="utf-8").strip() if port_lock.exists() else ""
        previous_port_lock.write_text(current_port, encoding="utf-8")
    port_lock.write_text(f"{prototype.port}\n", encoding="utf-8")

    Prototype.objects.exclude(pk=prototype.pk).update(is_active=False)
    Prototype.objects.filter(pk=prototype.pk).update(is_active=True)
    prototype.is_active = True
    return prototype


def deactivate_prototype(*, base_dir: Path | None = None) -> None:
    """Clear the currently managed prototype environment without restarting."""

    resolved_base_dir = _base_dir(base_dir)
    _rewrite_managed_env_block(env_path(resolved_base_dir), OrderedDict())

    lock_path = active_prototype_lock_path(resolved_base_dir)
    if lock_path.exists():
        lock_path.unlink()

    port_lock = backend_port_lock_path(resolved_base_dir)
    previous_port_lock = previous_backend_port_lock_path(resolved_base_dir)
    previous_port = (
        previous_port_lock.read_text(encoding="utf-8").strip()
        if previous_port_lock.exists()
        else ""
    )
    if previous_port:
        port_lock.parent.mkdir(parents=True, exist_ok=True)
        port_lock.write_text(previous_port + "\n", encoding="utf-8")
    elif port_lock.exists():
        port_lock.unlink()
    if previous_port_lock.exists():
        previous_port_lock.unlink()

    Prototype.objects.filter(is_active=True).update(is_active=False)


def scaffold_package_root(base_dir: Path | None = None) -> Path:
    return _apps_dir(base_dir) / "_prototypes"


def scaffold_app_dir(prototype: Prototype, *, base_dir: Path | None = None) -> Path:
    return scaffold_package_root(base_dir) / prototype.slug


def _camelize(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_"))


def scaffold_prototype_app(prototype: Prototype, *, base_dir: Path | None = None) -> Path:
    """Create a hidden local Django app scaffold for *prototype* if missing."""

    resolved_base_dir = _base_dir(base_dir)
    apps_dir = _apps_dir(resolved_base_dir)
    hidden_root = scaffold_package_root(resolved_base_dir)
    app_dir = scaffold_app_dir(prototype, base_dir=resolved_base_dir)
    if app_dir.exists():
        return app_dir

    apps_dir.mkdir(parents=True, exist_ok=True)
    apps_init = apps_dir / "__init__.py"
    if not apps_init.exists():
        apps_init.write_text('"""Project application packages."""\n', encoding="utf-8")

    hidden_root.mkdir(parents=True, exist_ok=True)
    hidden_init = hidden_root / "__init__.py"
    if not hidden_init.exists():
        hidden_init.write_text('"""Hidden prototype app packages."""\n', encoding="utf-8")

    model_name = f"{_camelize(prototype.slug)}Experiment"
    config_class = f"{_camelize(prototype.slug)}PrototypeConfig"
    verbose_name = prototype.name.replace('"', '\\"')

    files_to_write: dict[Path, str] = {
        app_dir / "__init__.py": '"""Scaffolded prototype app."""\n',
        app_dir / "apps.py": (
            "from django.apps import AppConfig\n\n\n"
            f"class {config_class}(AppConfig):\n"
            '    """App config for a scaffolded hidden prototype app."""\n\n'
            '    default_auto_field = "django.db.models.BigAutoField"\n'
            f'    name = "{prototype.scaffold_module}"\n'
            f'    label = "{prototype.scaffold_label}"\n'
            f'    verbose_name = "{verbose_name}"\n'
        ),
        app_dir / "models.py": (
            "from django.db import models\n\n\n"
            f"class {model_name}(models.Model):\n"
            '    """Starter model for the scaffolded prototype app."""\n\n'
            "    name = models.CharField(max_length=120, unique=True)\n"
            "    created_at = models.DateTimeField(auto_now_add=True)\n\n"
            "    class Meta:\n"
            f'        verbose_name = "{prototype.name} record"\n'
            f'        verbose_name_plural = "{prototype.name} records"\n\n'
            "    def __str__(self) -> str:\n"
            "        return self.name\n"
        ),
        app_dir / "admin.py": (
            "from django.contrib import admin\n\n"
            f"from .models import {model_name}\n\n\n"
            f"@admin.register({model_name})\n"
            "class PrototypeRecordAdmin(admin.ModelAdmin):\n"
            '    """Admin surface for the scaffolded prototype starter model."""\n\n'
            '    list_display = ("name", "created_at")\n'
            '    search_fields = ("name",)\n'
        ),
        app_dir / "views.py": (
            "from django.http import HttpRequest, HttpResponse\n\n\n"
            "def index(request: HttpRequest) -> HttpResponse:\n"
            '    """Return a minimal response so scaffolded prototype routes are immediately usable."""\n\n'
            f'    return HttpResponse("Prototype scaffold active: {prototype.slug}")\n'
        ),
        app_dir / "urls.py": (
            "from django.urls import path\n\nfrom . import views\n\n\n"
            "urlpatterns = [\n"
            '    path("", views.index, name="index"),\n'
            "]\n"
        ),
        app_dir / "routes.py": (
            "from django.urls import include, path\n\n\n"
            "ROOT_URLPATTERNS = [\n"
            f'    path("prototype/{prototype.slug}/", include("{prototype.scaffold_module}.urls")),\n'
            "]\n"
        ),
        app_dir / "migrations" / "__init__.py": "",
        app_dir / "tests" / "__init__.py": '"""Tests for scaffolded prototype apps."""\n',
        app_dir / "tests" / f"test_{prototype.slug}_smoke.py": (
            "def test_placeholder() -> None:\n"
            '    """Placeholder smoke test for the scaffolded prototype app."""\n\n'
            "    assert True\n"
        ),
    }

    for path, content in files_to_write.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    return app_dir


def restart_suite(*, base_dir: Path | None = None, force_stop: bool = False) -> None:
    """Restart the suite via the existing lifecycle scripts."""

    resolved_base_dir = _base_dir(base_dir)
    stop_command = ["./stop.sh"]
    if force_stop:
        stop_command.append("--force")
    subprocess.run(stop_command, cwd=resolved_base_dir, check=True)
    subprocess.run(["./start.sh", "--await"], cwd=resolved_base_dir, check=True)
