from .common_imports import *


class CertificateRequestAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "action",
        "certificate_type",
        "status",
        "requested_at",
        "responded_at",
    )
    list_filter = ("action", "status", "certificate_type")
    search_fields = ("charger__charger_id", "certificate_type", "status_info")
    date_hierarchy = "requested_at"


class CertificateStatusCheckAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "status",
        "requested_at",
        "responded_at",
    )
    list_filter = ("status",)
    search_fields = ("charger__charger_id", "status_info")
    date_hierarchy = "requested_at"


class CertificateOperationAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "action",
        "certificate_type",
        "status",
        "requested_at",
        "responded_at",
    )
    list_filter = ("action", "status", "certificate_type")
    search_fields = (
        "charger__charger_id",
        "certificate_type",
        "status_info",
        "error_code",
    )
    date_hierarchy = "requested_at"


class InstalledCertificateAdmin(EntityModelAdmin):
    list_display = (
        "charger",
        "certificate_type",
        "status",
        "installed_at",
        "deleted_at",
    )
    list_filter = ("status", "certificate_type")
    search_fields = ("charger__charger_id", "certificate_type")


class TrustAnchorAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "charger",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "charger__charger_id")


admin.site.register(CertificateRequest, CertificateRequestAdmin)
admin.site.register(CertificateStatusCheck, CertificateStatusCheckAdmin)
admin.site.register(CertificateOperation, CertificateOperationAdmin)
admin.site.register(InstalledCertificate, InstalledCertificateAdmin)
admin.site.register(TrustAnchor, TrustAnchorAdmin)
