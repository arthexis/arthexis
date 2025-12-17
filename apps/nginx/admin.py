from pathlib import Path

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.nginx import services
from apps.nginx.models import SiteConfiguration
from apps.nginx.renderers import generate_primary_config, generate_site_entries_content


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "enabled",
        "mode",
        "role",
        "port",
        "include_ipv6",
        "last_applied_at",
        "last_validated_at",
    )
    list_filter = ("enabled", "mode", "include_ipv6")
    search_fields = ("name", "role")
    readonly_fields = ("last_applied_at", "last_validated_at", "last_message")
    actions = [
        "apply_configurations",
        "validate_configurations",
        "preview_configurations",
    ]

    def get_urls(self):  # pragma: no cover - admin hook
        custom = [
            path(
                "preview/",
                self.admin_site.admin_view(self.preview_view),
                name="nginx_siteconfiguration_preview",
            ),
        ]
        return custom + super().get_urls()

    @admin.action(description=_("Apply selected configurations"))
    def apply_configurations(self, request, queryset):
        for config in queryset:
            try:
                result = config.apply()
            except (services.NginxUnavailableError, services.ValidationError) as exc:
                self.message_user(request, f"{config}: {exc}", messages.ERROR)
                continue

            level = messages.SUCCESS if result.validated else messages.INFO
            self.message_user(request, f"{config}: {result.message}", level)

    @admin.action(description=_("Validate selected configurations"))
    def validate_configurations(self, request, queryset):
        for config in queryset:
            try:
                result = config.validate_only()
            except services.NginxUnavailableError as exc:
                self.message_user(request, f"{config}: {exc}", messages.ERROR)
                continue

            level = messages.SUCCESS if result.validated else messages.INFO
            self.message_user(request, f"{config}: {result.message}", level)

    @admin.action(description=_("Preview selected configurations"))
    def preview_configurations(self, request, queryset):
        selected = queryset.values_list("pk", flat=True)
        ids = ",".join(str(pk) for pk in selected)
        url = reverse("admin:nginx_siteconfiguration_preview")
        return HttpResponseRedirect(f"{url}?ids={ids}")

    def preview_view(self, request: HttpRequest):
        if not self.has_view_permission(request):
            return self._unauthorized(request)

        ids = request.GET.get("ids", "")
        if ids:
            pk_values = [pk for pk in ids.split(",") if pk]
            queryset = self.get_queryset(request).filter(pk__in=pk_values)
        else:
            queryset = self.get_queryset(request).none()

        config_previews = [
            {
                "config": config,
                "files": self._build_file_previews(config),
            }
            for config in queryset
        ]

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Preview nginx configurations"),
            "config_previews": config_previews,
            "media": self.media,
        }

        return TemplateResponse(
            request, "admin/nginx/siteconfiguration/preview.html", context
        )

    def _build_file_previews(self, config: SiteConfiguration) -> list[dict]:
        files: list[dict] = []

        primary_content = generate_primary_config(
            config.mode, config.port, include_ipv6=config.include_ipv6
        )
        files.append(
            self._build_file_preview(
                label=_("Primary configuration"),
                path=config.expected_destination,
                content=primary_content,
            )
        )

        try:
            site_content = generate_site_entries_content(
                config.staged_site_config, config.mode, config.port
            )
        except ValueError as exc:
            files.append(
                {
                    "label": _("Managed site server blocks"),
                    "path": config.site_destination_path,
                    "content": "",
                    "status": str(exc),
                }
            )
        else:
            files.append(
                self._build_file_preview(
                    label=_("Managed site server blocks"),
                    path=config.site_destination_path,
                    content=site_content,
                )
            )

        return files

    def _build_file_preview(self, *, label: str, path: Path, content: str) -> dict:
        status = self._get_file_status(path, content)
        return {"label": label, "path": path, "content": content, "status": status}

    def _get_file_status(self, path: Path, content: str) -> str:
        try:
            existing = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _("File does not exist on disk.")
        except OSError:
            return _("Existing file could not be read.")

        if existing == content:
            return _("Existing file already matches this content.")

        return _("Existing file differs and would be updated.")
