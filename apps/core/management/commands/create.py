"""Unified project scaffolding command for apps and models."""

from __future__ import annotations

import keyword
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

APP_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*$")
MODEL_APP_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class Command(BaseCommand):
    """Create local apps or model scaffolds within existing local apps."""

    help = "Bootstrap project-local apps and models with common wiring."

    def add_arguments(self, parser):
        """Register subcommands for app and model generation."""

        subparsers = parser.add_subparsers(dest="target")
        subparsers.required = True

        app_parser = subparsers.add_parser("app", help="Create a new local app scaffold.")
        app_parser.add_argument("name", help="App package name (lowercase single word, no underscores).")
        app_parser.add_argument(
            "--backend-only",
            action="store_true",
            help="Create an app scaffold without views.py, urls.py, and routes.py.",
        )
        app_parser.add_argument("--apps-dir", dest="apps_dir", help="Override apps directory path.")

        model_parser = subparsers.add_parser(
            "model", help="Create a model scaffold inside an existing local app."
        )
        model_parser.add_argument("app", help="Existing app package name (example: billing or billing_tools).")
        model_parser.add_argument("name", help="Model name in snake_case or CamelCase.")
        model_parser.add_argument("--apps-dir", dest="apps_dir", help="Override apps directory path.")

    def handle(self, *args, **options):
        """Dispatch create actions."""

        target = options["target"]
        apps_dir = self._get_apps_dir(options.get("apps_dir"))

        if target == "app":
            app_name = str(options["name"]).strip()
            self._validate_app_name(app_name)
            self._create_app(apps_dir, app_name, backend_only=bool(options.get("backend_only")))
            return

        if target == "model":
            app_name = str(options["app"]).strip()
            self._validate_model_app_name(app_name)
            raw_model_name = str(options["name"]).strip()
            model_name = self._normalize_model_name(raw_model_name)
            self._create_model(apps_dir, app_name, model_name)
            return

        raise CommandError(f"Unsupported create target: {target}")

    def _get_apps_dir(self, apps_dir_option: str | None) -> Path:
        return Path(apps_dir_option or getattr(settings, "APPS_DIR", Path(settings.BASE_DIR) / "apps"))

    def _create_app(self, apps_dir: Path, app_name: str, *, backend_only: bool) -> None:
        app_dir = apps_dir / app_name
        if app_dir.exists():
            raise CommandError(f"App already exists: {app_dir}")

        self._ensure_apps_package(apps_dir)
        model_name = f"{self._camelize(app_name)}Item"
        app_config_class = f"{self._camelize(app_name)}Config"

        files_to_write: dict[Path, str] = {
            app_dir / "__init__.py": '"""Local app package."""\n',
            app_dir / "apps.py": self._apps_py(app_config_class, app_name),
            app_dir / "models.py": self._model_class_block(model_name, app_name, include_import=True),
            app_dir / "admin.py": self._admin_registration_block(model_name, include_imports=True),
            app_dir / "manifest.py": self._manifest_py(app_name, backend_only=backend_only),
            app_dir / "migrations" / "__init__.py": "",
            app_dir / "tests" / "__init__.py": '"""Tests for scaffolded app modules."""\n',
            app_dir / "tests" / f"test_{app_name}_smoke.py": self._app_test_py(app_name, backend_only),
        }

        if not backend_only:
            files_to_write[app_dir / "views.py"] = self._views_block(
                app_name,
                model_name,
                include_imports=True,
            )
            files_to_write[app_dir / "urls.py"] = self._urls_block(model_name, include_imports=True)
            files_to_write[app_dir / "routes.py"] = self._routes_with_urls(app_name)

        for path, content in files_to_write.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(f"Created app scaffold at {app_dir}"))
        self._print_post_create_steps(app_name)

    def _create_model(self, apps_dir: Path, app_name: str, model_name: str) -> None:
        app_dir = apps_dir / app_name
        if not app_dir.exists():
            raise CommandError(f"App does not exist: {app_dir}")
        backend_only = self._is_backend_only_app(app_dir)

        models_path = app_dir / "models.py"
        self._append_unique_block(
            models_path,
            marker=f"class {model_name}(models.Model)",
            block=self._model_class_block(model_name, app_name, include_import=not models_path.exists()),
        )

        admin_path = app_dir / "admin.py"
        self._append_unique_block(
            admin_path,
            marker=f"@admin.register({model_name})",
            block=self._admin_registration_block(model_name, include_imports=not admin_path.exists()),
        )

        if not backend_only:
            views_path = app_dir / "views.py"
            self._append_unique_block(
                views_path,
                marker=f"class {model_name}ListView(ListView)",
                block=self._views_block(app_name, model_name, include_imports=not views_path.exists()),
            )

            self._ensure_urls_include_model(app_dir / "urls.py", model_name)
            self._ensure_routes_include(app_dir / "routes.py", app_name)

        self.stdout.write(self.style.SUCCESS(f"Scaffolded model {model_name} in apps/{app_name}/"))
        self.stdout.write("\nPost-create checklist:")
        self.stdout.write(f"1. Run `python manage.py makemigrations {app_name}` then `python manage.py migrate`.")
        if backend_only:
            self.stdout.write("2. Adjust generated fields and admin list_display to your domain.")
            self.stdout.write(
                "3. Backend-only marker detected in manifest.py; skipped views.py, urls.py, and routes.py wiring."
            )
        else:
            self.stdout.write("2. Adjust generated fields, admin list_display, and views to your domain.")
            self.stdout.write(
                f"3. Add templates under apps/{app_name}/templates/{app_name}/ for the new views."
            )

    def _is_backend_only_app(self, app_dir: Path) -> bool:
        manifest_path = app_dir / "manifest.py"
        if not manifest_path.exists():
            return False
        return "APP_STRUCTURE: backend-only" in manifest_path.read_text(encoding="utf-8")

    def _append_unique_block(self, path: Path, marker: str, block: str) -> None:
        if path.exists():
            current = path.read_text(encoding="utf-8")
            if marker in current:
                raise CommandError(f"Refusing to modify {path}: marker already exists ({marker}).")
            if block.strip() in current:
                return
            path.write_text(current.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(block, encoding="utf-8")

    def _ensure_routes_include(self, routes_path: Path, app_name: str) -> None:
        include_stmt = "from django.urls import include, path"
        route_line = f'    path("{app_name}/", include("apps.{app_name}.urls")),'
        if not routes_path.exists():
            routes_path.write_text(self._routes_with_urls(app_name), encoding="utf-8")
            return

        content = routes_path.read_text(encoding="utf-8")
        if route_line in content:
            return

        if include_stmt not in content:
            if "from django.urls import path" in content:
                content = content.replace("from django.urls import path", include_stmt, 1)
            else:
                content = include_stmt + "\n\n" + content.lstrip()

        marker = "ROOT_URLPATTERNS = [\n"
        if marker in content:
            content = content.replace(marker, marker + route_line + "\n", 1)
        elif re.search(r"ROOT_URLPATTERNS\s*=\s*\[\s*\]", content):
            content = re.sub(
                r"ROOT_URLPATTERNS\s*=\s*\[\s*\]",
                "ROOT_URLPATTERNS = [\n" + route_line + "\n]",
                content,
                count=1,
            )
        else:
            content += (
                "\n\nROOT_URLPATTERNS = [\n"
                + route_line
                + "\n]\n"
            )

        routes_path.write_text(content, encoding="utf-8")

    def _ensure_urls_include_model(self, urls_path: Path, model_name: str) -> None:
        marker = f'name="{self._model_slug(model_name)}-list"'
        if not urls_path.exists():
            self._append_unique_block(
                urls_path,
                marker=marker,
                block=self._urls_block(model_name, include_imports=True),
            )
            return

        content = urls_path.read_text(encoding="utf-8")
        if marker in content:
            raise CommandError(f"Refusing to modify {urls_path}: marker already exists ({marker}).")

        import_lines = ["from django.urls import path", "from . import views"]
        missing_imports = [line for line in import_lines if line not in content]
        if missing_imports:
            content = "\n".join(missing_imports) + "\n\n" + content.lstrip()

        model_routes = self._urls_model_routes(model_name)
        urlpatterns_match = re.search(r"urlpatterns\s*=\s*\[", content)
        if urlpatterns_match:
            insert_at = content.find("]", urlpatterns_match.end())
            if insert_at != -1:
                content = content[:insert_at].rstrip() + "\n" + model_routes + "\n" + content[insert_at:]
                urls_path.write_text(content, encoding="utf-8")
                return

        content = content.rstrip() + "\n\nurlpatterns = [\n" + model_routes + "\n]\n"
        urls_path.write_text(content, encoding="utf-8")

    def _validate_app_name(self, value: str) -> None:
        if not value:
            raise CommandError("App name cannot be empty.")
        if keyword.iskeyword(value):
            raise CommandError(f"Invalid app name '{value}': Python keyword.")
        if not APP_NAME_PATTERN.match(value):
            raise CommandError(
                f"Invalid app name '{value}'. Use a lowercase single word (letters and digits only), starting with a letter."
            )

    def _validate_model_app_name(self, value: str) -> None:
        if not value:
            raise CommandError("App name cannot be empty.")
        if keyword.iskeyword(value):
            raise CommandError(f"Invalid app name '{value}': Python keyword.")
        if not MODEL_APP_NAME_PATTERN.match(value):
            raise CommandError(
                f"Invalid app name '{value}'. Use a lowercase identifier (letters, digits, and underscores only), starting with a letter."
            )

    def _normalize_model_name(self, value: str) -> str:
        if not value:
            raise CommandError("Model name cannot be empty.")
        if "_" in value or value.islower():
            value = self._camelize(value)
        if not re.match(r"^[A-Z][A-Za-z0-9]*$", value):
            raise CommandError(
                f"Invalid model name '{value}'. Use CamelCase or snake_case for conversion."
            )
        return value

    def _ensure_apps_package(self, apps_dir: Path) -> None:
        apps_dir.mkdir(parents=True, exist_ok=True)
        apps_init = apps_dir / "__init__.py"
        if not apps_init.exists():
            apps_init.write_text('"""Project application packages."""\n', encoding="utf-8")

    def _camelize(self, name: str) -> str:
        return "".join(part.capitalize() for part in name.split("_"))

    def _model_slug(self, model_name: str) -> str:
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", model_name).lower()
        return snake.replace("_", "-")

    def _apps_py(self, app_class_name: str, app_name: str) -> str:
        return (
            "from django.apps import AppConfig\n\n\n"
            f"class {app_class_name}(AppConfig):\n"
            '    """Default app configuration for scaffolded local app."""\n\n'
            '    default_auto_field = "django.db.models.BigAutoField"\n'
            f'    name = "apps.{app_name}"\n'
            f'    label = "{app_name}"\n'
            f'    verbose_name = "{app_name.replace("_", " ").title()}"\n'
        )

    def _model_class_block(self, model_name: str, app_name: str, *, include_import: bool) -> str:
        import_block = "from django.db import models\n\n\n" if include_import else ""
        return (
            import_block
            + f"class {model_name}(models.Model):\n"
            + '    """Starter model generated by the create command."""\n\n'
            + "    name = models.CharField(max_length=120, unique=True)\n"
            + "    created_at = models.DateTimeField(auto_now_add=True)\n\n"
            + "    class Meta:\n"
            + f"        app_label = \"{app_name}\"\n"
            + "        ordering = (\"name\",)\n\n"
            + "    def __str__(self) -> str:\n"
            + "        return self.name\n"
        )

    def _admin_registration_block(self, model_name: str, *, include_imports: bool) -> str:
        import_block = (
            "from django.contrib import admin\n\n"
            f"from .models import {model_name}\n\n\n"
            if include_imports
            else ""
        )
        return (
            import_block
            + f"@admin.register({model_name})\n"
            + f"class {model_name}Admin(admin.ModelAdmin):\n"
            + '    """Starter admin registration for generated model."""\n\n'
            + "    list_display = (\"name\", \"created_at\")\n"
            + "    search_fields = (\"name\",)\n"
        )

    def _views_block(self, app_name: str, model_name: str, *, include_imports: bool) -> str:
        slug = self._model_slug(model_name)
        import_block = (
            "from django.views.generic import DetailView, ListView\n\n"
            f"from .models import {model_name}\n\n\n"
            if include_imports
            else ""
        )
        return (
            import_block
            + f"class {model_name}ListView(ListView):\n"
            + '    """Starter list view for generated model."""\n\n'
            + f"    model = {model_name}\n"
            + f'    template_name = "{app_name}/{slug}_list.html"\n'
            + f'    context_object_name = "{slug}_list"\n\n'
            + f"class {model_name}DetailView(DetailView):\n"
            + '    """Starter detail view for generated model."""\n\n'
            + f"    model = {model_name}\n"
            + f'    template_name = "{app_name}/{slug}_detail.html"\n'
            + f'    context_object_name = "{slug}"\n'
        )

    def _urls_block(self, model_name: str, *, include_imports: bool) -> str:
        import_block = (
            "from django.urls import path\n\n"
            "from . import views\n\n\n"
            if include_imports
            else ""
        )
        return import_block + "urlpatterns = [\n" + self._urls_model_routes(model_name) + "\n]\n"

    def _urls_model_routes(self, model_name: str) -> str:
        slug = self._model_slug(model_name)
        list_class = f"{model_name}ListView"
        detail_class = f"{model_name}DetailView"
        return (
            f"    path(\"{slug}/\", views.{list_class}.as_view(), name=\"{slug}-list\"),\n"
            f"    path(\"{slug}/<int:pk>/\", views.{detail_class}.as_view(), name=\"{slug}-detail\"),"
        )

    def _manifest_py(self, app_name: str, *, backend_only: bool) -> str:
        marker = "# APP_STRUCTURE: backend-only (intentionally omits views.py, urls.py, and routes.py)\n"
        return (
            '"""Manifest entries for Django app loading."""\n\n'
            + (marker if backend_only else "")
            + "DJANGO_APPS = [\n"
            f'    "apps.{app_name}",\n'
            "]\n"
        )

    def _routes_with_urls(self, app_name: str) -> str:
        return (
            '"""Root route provider for app-owned URL mounts."""\n\n'
            "from django.urls import include, path\n\n"
            "ROOT_URLPATTERNS = [\n"
            f'    path("{app_name}/", include("apps.{app_name}.urls")),\n'
            "]\n"
        )

    def _app_test_py(self, app_name: str, backend_only: bool) -> str:
        web_assertions = ""
        if not backend_only:
            web_assertions = (
                f'    assert import_module("apps.{app_name}.views")\n'
                f'    assert import_module("apps.{app_name}.urls")\n'
            )
        return (
            '"""Starter smoke tests for generated app modules."""\n\n'
            "from importlib import import_module\n\n\n"
            f"def test_{app_name}_imports() -> None:\n"
            '    """Generated app modules should be importable."""\n\n'
            f'    assert import_module("apps.{app_name}.apps")\n'
            f'    assert import_module("apps.{app_name}.manifest")\n'
            f'    assert import_module("apps.{app_name}.models")\n'
            + web_assertions
        )

    def _print_post_create_steps(self, app_name: str) -> None:
        self.stdout.write("\nPost-create checklist:")
        self.stdout.write(f"1. Add 'apps.{app_name}' to your enabled app manifests if needed.")
        self.stdout.write(f"2. Run `python manage.py makemigrations {app_name}` then `python manage.py migrate`.")
        self.stdout.write(
            f"3. If this app serves web endpoints, review apps/{app_name}/routes.py and apps/{app_name}/urls.py for URL mounting requirements."
        )
        self.stdout.write(f"4. Add templates in apps/{app_name}/templates/{app_name}/ and expand tests.")
        self.stdout.write(f"5. Add fixtures under apps/{app_name}/fixtures/ as needed.")
