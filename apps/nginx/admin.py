from pathlib import Path

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.certs.models import CertificateBase
from apps.nginx import services
from apps.nginx.models import SiteConfiguration
from apps.nginx.renderers import generate_primary_config, generate_site_entries_content


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "enabled",
        "mode",
        "protocol",
        "role",
        "port",
        "certificate",
        "include_ipv6",
        "last_applied_at",
        "last_validated_at",
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
                "generate-certificates/",
                self.admin_site.admin_view(self.generate_certificates_view),
                name="nginx_siteconfiguration_generate_certificates",
            ),
        ]
        return custom + super().get_urls()

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
            return HttpResponseRedirect(redirect_url)

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
        }

        return TemplateResponse(
            request, "admin/nginx/siteconfiguration/preview.html", context
        )

    def generate_certificates_view(self, request: HttpRequest):
        if not self.has_change_permission(request):
            raise PermissionDenied

        ids_param, _, queryset = self._get_selection_from_request(request)

        if request.method == "POST":
            self._generate_certificates(request, queryset, ids_param)
            redirect_url = reverse("admin:nginx_siteconfiguration_preview")
            if ids_param:
                redirect_url = f"{redirect_url}?ids={ids_param}"
            return HttpResponseRedirect(redirect_url)

        return HttpResponseRedirect(reverse("admin:nginx_siteconfiguration_changelist"))

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

    def _build_file_previews(self, config: SiteConfiguration) -> list[dict]:
        files: list[dict] = []

        primary_content = generate_primary_config(
            config.mode,
            config.port,
            certificate=config.certificate,
            https_enabled=config.protocol == "https",
            include_ipv6=config.include_ipv6,
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

    def _warn_missing_certificate(self, request: HttpRequest, config: SiteConfiguration, ids_param: str):
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

    def _generate_certificates(self, request: HttpRequest, queryset, ids_param: str = ""):
        for config in queryset:
            if config.protocol != "https":
                self.message_user(
                    request,
                    _("%s: HTTPS is not enabled; skipping certificate provisioning.") % config,
                    messages.INFO,
                )
                continue

            certificate: CertificateBase | None = config.certificate
            if certificate is None:
                self._warn_missing_certificate(request, config, ids_param)
                continue

            try:
                message = certificate.provision()
            except Exception as exc:  # pragma: no cover - admin plumbing
                self.message_user(request, f"{config}: {exc}", messages.ERROR)
            else:
                self.message_user(
                    request,
                    _("%s: %s") % (config, message),
                    messages.SUCCESS,
                )

    @admin.action(description=_("Generate certificates"))
    def generate_certificates(self, request, queryset):
        self._generate_certificates(request, queryset)

    def _find_missing_certificates(self, queryset):
        missing = [config for config in queryset if config.protocol == "https" and config.certificate is None]
        return missing
