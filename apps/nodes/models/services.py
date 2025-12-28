from __future__ import annotations

import getpass
import os
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity
from .slug_entities import SlugDisplayNaturalKeyMixin, SlugEntityManager

SERVICE_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "service_templates"


def _systemd_directory() -> Path:
    return Path(os.environ.get("SYSTEMD_DIR", "/etc/systemd/system"))


class NodeServiceManager(SlugEntityManager):
    pass


class NodeService(SlugDisplayNaturalKeyMixin, Entity):
    """Expected service managed on a node with its systemd template."""

    slug = models.SlugField(max_length=50, unique=True)
    display = models.CharField(max_length=100)
    unit_template = models.CharField(
        max_length=150,
        help_text=_("Pattern for the systemd unit name (for example {service_name}.service)."),
    )
    feature = models.ForeignKey(
        "nodes.NodeFeature",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=_("Limit this service to nodes with the selected feature."),
    )
    is_required = models.BooleanField(
        default=False,
        help_text=_("Mark as required when the service should always be present."),
    )
    template_path = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Path to the service template stored in the repository."),
    )
    template_content = models.TextField(
        blank=True,
        help_text=_("Stored copy of the service template for reference."),
    )

    objects = NodeServiceManager()

    class Meta:
        ordering = ["display"]
        verbose_name = "Node Service"
        verbose_name_plural = "Node Services"

    @staticmethod
    def detect_service_name(base_dir: Path) -> str:
        service_file = base_dir / ".locks" / "service.lck"
        try:
            return service_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @staticmethod
    def detect_service_user(base_dir: Path) -> str:
        try:
            return base_dir.owner()
        except Exception:
            try:
                return getpass.getuser()
            except Exception:
                return ""

    def get_template_path(self) -> Path | None:
        if not self.template_path:
            return None
        candidate = Path(self.template_path)
        if not candidate.is_absolute():
            candidate = SERVICE_TEMPLATE_DIR / candidate
        return candidate

    def get_template_body(self) -> str:
        template_path = self.get_template_path()
        if template_path:
            try:
                return template_path.read_text(encoding="utf-8")
            except OSError:
                pass
        return self.template_content or ""

    def build_context(
        self,
        *,
        base_dir: Path | None = None,
        extra_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        base_path = base_dir or Path(settings.BASE_DIR)
        context = {
            "base_dir": str(base_path),
            "service_name": self.detect_service_name(base_path),
            "service_user": self.detect_service_user(base_path),
            "exec_command": str(base_path / "scripts" / "service-start.sh"),
        }
        if extra_context:
            context.update(extra_context)
        return context

    def resolve_unit_name(self, context: dict[str, object] | None = None) -> str:
        context_data = context or self.build_context()
        template = self.unit_template or ""
        if not template:
            return ""
        try:
            unit_name = template.format(**context_data)
        except Exception:
            unit_name = template
        if not unit_name.endswith(".service"):
            unit_name = f"{unit_name}.service"
        return unit_name

    def render_template(self, context: dict[str, object] | None = None) -> str:
        context_data = context or self.build_context()
        template = self.get_template_body()
        if not template:
            return ""
        try:
            return template.format(**context_data)
        except Exception:
            return template

    @staticmethod
    def _normalize_content(value: str) -> str:
        return "\n".join(line.rstrip() for line in value.splitlines()).strip()

    def compare_to_installed(
        self,
        *,
        base_dir: Path | None = None,
        service_dir: Path | None = None,
        context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        base_path = base_dir or Path(settings.BASE_DIR)
        context_data = self.build_context(base_dir=base_path, extra_context=context)
        unit_name = self.resolve_unit_name(context_data)
        expected = self.render_template(context_data)
        service_directory = service_dir or _systemd_directory()

        if not unit_name:
            return {
                "unit_name": self.unit_template,
                "matches": False,
                "status": str(_("Missing service name for template resolution.")),
                "expected": expected,
                "actual": "",
            }

        service_file = service_directory / unit_name
        try:
            actual = service_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {
                "unit_name": unit_name,
                "matches": False,
                "status": str(_("Service file not found.")),
                "expected": expected,
                "actual": "",
            }
        except OSError:
            return {
                "unit_name": unit_name,
                "matches": False,
                "status": str(_("Unable to read service file.")),
                "expected": expected,
                "actual": "",
            }

        matches = self._normalize_content(expected) == self._normalize_content(actual)
        status = ""
        if not matches:
            status = str(_("Installed configuration differs from the template."))

        return {
            "unit_name": unit_name,
            "matches": matches,
            "status": status,
            "expected": expected,
            "actual": actual,
        }
