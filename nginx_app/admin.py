from django.contrib import admin
from django.contrib import messages
from django.utils.html import format_html
from .models import NginxConfig


@admin.register(NginxConfig)
class NginxConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "server_name", "primary_upstream", "backup_upstream")
    actions = ["test_configuration"]
    fields = (
        "name",
        "server_name",
        "primary_upstream",
        "backup_upstream",
        "listen_port",
        "ssl_certificate",
        "ssl_certificate_key",
        "rendered_config",
    )
    readonly_fields = ("rendered_config",)

    @admin.action(description="Test selected NGINX templates")
    def test_configuration(self, request, queryset):
        for cfg in queryset:
            if cfg.test_connection():
                self.message_user(request, f"{cfg.name} reachable", messages.SUCCESS)
            else:
                self.message_user(request, f"{cfg.name} unreachable", messages.ERROR)

    @admin.display(description="Generated config")
    def rendered_config(self, obj):
        return format_html(
            '<textarea readonly style="width:100%" rows="20">{}</textarea>',
            obj.config_text,
        )
