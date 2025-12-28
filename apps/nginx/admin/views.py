from __future__ import annotations

from pathlib import Path

from django.contrib import admin, messages
from django.contrib.humanize.templatetags.humanize import naturaltime
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.nginx import services
from apps.nginx.renderers import generate_primary_config, generate_site_entries_content


class SiteConfigurationViewMixin:
    change_list_template = "admin/nginx/siteconfiguration/change_list.html"
    list_display = (
        "name",
        "enabled",
        "mode",
        "protocol",
        "role",
        "port",
        "certificate",
        "include_ipv6",
        "last_sync_at",
    )
    list_filter = ("enabled", "mode", "protocol", "include_ipv6")
    search_fields = ("name", "role", "certificate__name")
    readonly_fields = ("last_applied_at", "last_validated_at", "last_message")
    actions = [
        "validate_configurations",
        "preview_configurations",
        "generate_certificates",
    ]

    def get_urls(self):  # pragma: no cover - admin hook
        custom = [
            path(
                "preview/",
                self.admin_site.admin_view(self.preview_view),
                name="nginx_siteconfiguration_preview",
            ),
            path(
                "preview-default/",
                self.admin_site.admin_view(self.preview_default_view),
                name="nginx_siteconfiguration_preview_default",
            ),
            path(
                "generate-certificates/",
                self.admin_site.admin_view(self.generate_certificates_view),
                name="nginx_siteconfiguration_generate_certificates",
            ),
        ]
        return custom + super().get_urls()

    @admin.display(description=_("Last sync at"))
    def last_sync_at(self, obj):
        latest = max(
            (value for value in (obj.last_applied_at, obj.last_validated_at) if value),
            default=None,
        )
        if not latest:
            return "-"
        return naturaltime(latest)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["default_preview_url"] = reverse(
            "admin:nginx_siteconfiguration_preview_default"
        )
        return super().changelist_view(request, extra_context=extra_context)

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
        return self._http_redirect(f"{url}?ids={ids}")

    def preview_view(self, request: HttpRequest):
        if not self.has_view_permission(request):
            raise PermissionDenied

        ids_param, _pk_values, queryset = self._get_selection_from_request(request)
        missing_certificates = self._find_missing_certificates(queryset)

        if request.method == "POST":
            if not self.has_change_permission(request):
                raise PermissionDenied
            self._apply_configurations(request, queryset, ids_param)
            redirect_url = reverse("admin:nginx_siteconfiguration_preview")
            if ids_param:
                redirect_url = f"{redirect_url}?ids={ids_param}"
            return self._http_redirect(redirect_url)

        return self._render_preview(
            request,
            queryset=queryset,
            ids_param=ids_param,
            missing_certificates=missing_certificates,
        )

    def preview_default_view(self, request: HttpRequest):
        if not self.has_view_permission(request):
            raise PermissionDenied

        default_config = self.model.get_default()
        queryset = self.get_queryset(request).filter(pk=default_config.pk)
        ids_param = str(default_config.pk)
        missing_certificates = self._find_missing_certificates(queryset)

        if request.method == "POST":
            if not self.has_change_permission(request):
                raise PermissionDenied
            self._apply_configurations(request, queryset, ids_param)
            return self._http_redirect(
                reverse("admin:nginx_siteconfiguration_preview_default")
            )

        return self._render_preview(
            request,
            queryset=queryset,
            ids_param=ids_param,
            missing_certificates=missing_certificates,
        )

    def _render_preview(
        self,
        request: HttpRequest,
        *,
        queryset,
        ids_param: str,
        missing_certificates,
    ):
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
            "ids_param": ids_param,
            "can_apply": self.has_change_permission(request),
            "missing_certificates": missing_certificates,
            "generate_certificates_url": reverse(
                "admin:nginx_siteconfiguration_generate_certificates"
            ),
            "certificate_type_choices": self._certificate_type_choices(),
            "default_certificate_type": self.default_certificate_type,
        }

        return TemplateResponse(
            request, "admin/nginx/siteconfiguration/preview.html", context
        )

    def _apply_configurations(self, request, queryset, ids_param: str = ""):
        for config in queryset:
            if config.protocol == "https" and config.certificate is None:
                self._warn_missing_certificate(request, config, ids_param)
                continue
            try:
                result = config.apply()
            except (services.NginxUnavailableError, services.ValidationError) as exc:
                self.message_user(request, f"{config}: {exc}", messages.ERROR)
                continue

            level = messages.SUCCESS if result.validated else messages.INFO
            self.message_user(request, f"{config}: {result.message}", level)

    def _build_file_previews(self, config) -> list[dict]:
        files: list[dict] = []

        primary_content = generate_primary_config(
            config.mode,
            config.port,
            certificate=config.certificate,
            https_enabled=config.protocol == "https",
            include_ipv6=config.include_ipv6,
            external_websockets=config.external_websockets,
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
                config.staged_site_config,
                config.mode,
                config.port,
                https_enabled=config.protocol == "https",
                external_websockets=config.external_websockets,
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

    def _get_selection_from_request(self, request: HttpRequest):
        ids_param = request.GET.get("ids", "") or request.POST.get("ids", "")
        pk_values: list[int] = []
        seen: set[int] = set()
        for value in ids_param.split(","):
            value = value.strip()
            if not value:
                continue
            try:
                pk_int = int(value)
            except ValueError:
                continue
            if pk_int in seen:
                continue
            seen.add(pk_int)
            pk_values.append(pk_int)

        if pk_values:
            queryset = self.get_queryset(request).filter(pk__in=pk_values)
        else:
            queryset = self.get_queryset(request).none()

        ids_param = ",".join(str(pk) for pk in pk_values)
        return ids_param, pk_values, queryset

    def _warn_missing_certificate(self, request: HttpRequest, config, ids_param: str):
        generate_url = reverse("admin:nginx_siteconfiguration_generate_certificates")
        if ids_param:
            generate_url = f"{generate_url}?ids={ids_param}"

        link = format_html(
            '<a href="{}">{}</a>',
            generate_url,
            _("Generate Certificates"),
        )
        message = _(
            "%(config)s requires a linked certificate before applying HTTPS. Use %(link)s after assigning one."
        ) % {"config": config, "link": link}
        self.message_user(request, message, messages.ERROR)

