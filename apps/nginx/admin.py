import ipaddress
from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.humanize.templatetags.humanize import naturaltime
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.certs.models import CertificateBase, CertbotCertificate, SelfSignedCertificate
from apps.nginx import services
from apps.nginx.config_utils import slugify
from apps.nginx.models import SiteConfiguration
from apps.nginx.renderers import generate_primary_config, generate_site_entries_content


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    CERTIFICATE_TYPE_SELF_SIGNED = "self-signed"
    CERTIFICATE_TYPE_CERTBOT = "certbot"

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
    def last_sync_at(self, obj: SiteConfiguration) -> str:
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

        return self._render_preview(
            request, queryset=queryset, ids_param=ids_param, missing_certificates=missing_certificates
        )

    def preview_default_view(self, request: HttpRequest):
        if not self.has_view_permission(request):
            raise PermissionDenied

        default_config = SiteConfiguration.get_default()
        queryset = self.get_queryset(request).filter(pk=default_config.pk)
        ids_param = str(default_config.pk)
        missing_certificates = self._find_missing_certificates(queryset)

        if request.method == "POST":
            if not self.has_change_permission(request):
                raise PermissionDenied
            self._apply_configurations(request, queryset, ids_param)
            return HttpResponseRedirect(
                reverse("admin:nginx_siteconfiguration_preview_default")
            )

        return self._render_preview(
            request, queryset=queryset, ids_param=ids_param, missing_certificates=missing_certificates
        )

    def _render_preview(
        self,
        request: HttpRequest,
        *,
        queryset,
        ids_param: str,
        missing_certificates: list[SiteConfiguration],
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
            "default_certificate_type": self.CERTIFICATE_TYPE_SELF_SIGNED,
        }

        return TemplateResponse(
            request, "admin/nginx/siteconfiguration/preview.html", context
        )

    def generate_certificates_view(self, request: HttpRequest):
        if not self.has_change_permission(request):
            raise PermissionDenied

        ids_param, _, queryset = self._get_selection_from_request(request)

        if request.method == "POST":
            certificate_type = self._normalize_certificate_type(
                request.POST.get("certificate_type")
            )
            self._generate_certificates(
                request, queryset, ids_param, certificate_type=certificate_type
            )
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

    def _generate_certificates(
        self,
        request: HttpRequest,
        queryset,
        ids_param: str = "",
        *,
        certificate_type: str = CERTIFICATE_TYPE_SELF_SIGNED,
    ):
        for config in queryset:
            if config.protocol != "https":
                config.protocol = "https"
                config.save(update_fields=["protocol"])
                self.message_user(
                    request,
                    _("%s: HTTPS enabled to allow certificate provisioning.") % config,
                    messages.INFO,
                )

            certificate: CertificateBase | None = config.certificate
            if certificate is None:
                certificate = self._create_certificate_for_config(
                    config, certificate_type=certificate_type
                )
                created_label = self._certificate_type_label(certificate_type)
                self.message_user(
                    request,
                    _("%(config)s: Created a %(type)s certificate for %(domain)s.")
                    % {"config": config, "type": created_label, "domain": certificate.domain},
                    messages.INFO,
                )

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

    def _create_certificate_for_config(
        self, config: SiteConfiguration, *, certificate_type: str
    ) -> CertificateBase:
        if certificate_type == self.CERTIFICATE_TYPE_CERTBOT:
            return self._create_certbot_certificate_for_config(config)
        return self._create_self_signed_certificate_for_config(config)

    def _create_self_signed_certificate_for_config(self, config: SiteConfiguration) -> CertificateBase:
        domain = self._get_default_certificate_domain()
        slug = slugify(domain)
        base_path = Path(settings.BASE_DIR) / "scripts" / "generated" / "certificates" / slug
        defaults = {
            "domain": domain,
            "certificate_path": str(base_path / "fullchain.pem"),
            "certificate_key_path": str(base_path / "privkey.pem"),
        }

        certificate, created = SelfSignedCertificate.objects.get_or_create(
            name=f"{config.name or 'nginx-site'}-{slug}",
            defaults=defaults,
        )

        updated_fields: list[str] = []
        if not created:
            for field, value in defaults.items():
                if getattr(certificate, field) != value:
                    setattr(certificate, field, value)
                    updated_fields.append(field)
            if updated_fields:
                certificate.save(update_fields=updated_fields)

        if config.certificate_id != certificate.id:
            config.certificate = certificate
            config.save(update_fields=["certificate"])

        return certificate

    def _create_certbot_certificate_for_config(self, config: SiteConfiguration) -> CertificateBase:
        domain = self._get_default_certificate_domain()
        slug = slugify(domain)
        defaults = {
            "domain": domain,
            "certificate_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
            "certificate_key_path": f"/etc/letsencrypt/live/{domain}/privkey.pem",
        }

        certificate, created = CertbotCertificate.objects.get_or_create(
            name=f"{config.name or 'nginx-site'}-{slug}-certbot",
            defaults=defaults,
        )

        updated_fields: list[str] = []
        if not created:
            for field, value in defaults.items():
                if getattr(certificate, field) != value:
                    setattr(certificate, field, value)
                    updated_fields.append(field)
            if updated_fields:
                certificate.save(update_fields=updated_fields)

        if config.certificate_id != certificate.id:
            config.certificate = certificate
            config.save(update_fields=["certificate"])

        return certificate

    def _get_default_certificate_domain(self) -> str:
        hosts = getattr(settings, "ALLOWED_HOSTS", []) or []
        candidates: list[str] = []
        for host in hosts:
            normalized = str(host or "").strip()
            if not normalized or normalized.startswith("."):
                continue
            if "/" in normalized:
                continue
            if normalized.startswith("[") and "]" in normalized:
                normalized = normalized.split("]", 1)[0].lstrip("[")
            elif ":" in normalized and normalized.count(":") == 1:
                normalized = normalized.rsplit(":", 1)[0]
            if not normalized:
                continue
            try:
                ipaddress.ip_address(normalized)
            except ValueError:
                candidates.append(normalized)
            else:
                continue

        for candidate in candidates:
            if "." in candidate:
                return candidate

        if candidates:
            return candidates[0]

        return "localhost"

    def _certificate_type_choices(self) -> tuple[tuple[str, str], ...]:
        return (
            (self.CERTIFICATE_TYPE_SELF_SIGNED, _("Self-signed")),
            (self.CERTIFICATE_TYPE_CERTBOT, _("Certbot")),
        )

    def _normalize_certificate_type(self, value: str | None) -> str:
        if value == self.CERTIFICATE_TYPE_CERTBOT:
            return value
        return self.CERTIFICATE_TYPE_SELF_SIGNED

    def _certificate_type_label(self, value: str) -> str:
        return dict(self._certificate_type_choices()).get(value, _("self-signed"))
