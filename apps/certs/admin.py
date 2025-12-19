from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.certs.models import CertbotCertificate, SelfSignedCertificate


class CertificateProvisioningMixin:
    @admin.action(description=_("Generate Certificates"))
    def generate_certificates(self, request, queryset):
        for certificate in queryset:
            try:
                message = certificate.provision()
            except Exception as exc:  # pragma: no cover - admin plumbing
                self.message_user(request, f"{certificate}: {exc}", messages.ERROR)
            else:
                self.message_user(request, f"{certificate}: {message}", messages.SUCCESS)

    @admin.action(description=_("Verify Certificates"))
    def verify_certificates(self, request, queryset):
        for certificate in queryset:
            try:
                result = certificate.verify()
            except Exception as exc:  # pragma: no cover - admin plumbing
                self.message_user(request, f"{certificate}: {exc}", messages.ERROR)
            else:
                level = messages.SUCCESS if result.ok else messages.ERROR
                self.message_user(request, f"{certificate}: {result.summary}", level)


@admin.register(CertbotCertificate)
class CertbotCertificateAdmin(CertificateProvisioningMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "domain",
        "email",
        "certificate_path",
        "last_requested_at",
    )
    search_fields = ("name", "domain", "email")
    readonly_fields = ("last_requested_at", "last_message")
    actions = ["generate_certificates", "request_certbot", "verify_certificates"]

    @admin.action(description=_("Request or renew with certbot"))
    def request_certbot(self, request, queryset):
        for certificate in queryset:
            try:
                message = certificate.request()
            except Exception as exc:  # pragma: no cover - admin plumbing
                self.message_user(request, f"{certificate}: {exc}", messages.ERROR)
            else:
                self.message_user(request, f"{certificate}: {message}", messages.SUCCESS)


@admin.register(SelfSignedCertificate)
class SelfSignedCertificateAdmin(CertificateProvisioningMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "domain",
        "certificate_path",
        "valid_days",
        "last_generated_at",
    )
    search_fields = ("name", "domain")
    readonly_fields = ("last_generated_at", "last_message")
    actions = ["generate_certificates", "generate_self_signed", "verify_certificates"]

    @admin.action(description=_("Generate self-signed certificate"))
    def generate_self_signed(self, request, queryset):
        for certificate in queryset:
            try:
                message = certificate.generate()
            except Exception as exc:  # pragma: no cover - admin plumbing
                self.message_user(request, f"{certificate}: {exc}", messages.ERROR)
            else:
                self.message_user(request, f"{certificate}: {message}", messages.SUCCESS)
