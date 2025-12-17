from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from apps.nginx import services
from apps.nginx.models import SiteConfiguration


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
    actions = ["apply_configurations", "validate_configurations"]

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
