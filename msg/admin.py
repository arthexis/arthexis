from django.contrib import admin

from .models import Message
from .notifications import notify


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "body", "node", "created")
    search_fields = ("subject", "body")
    ordering = ("-created",)
    actions = ["send_messages"]

    @admin.action(description="Send selected messages")
    def send_messages(self, request, queryset):
        for msg in queryset:
            notify(msg.subject, msg.body)
        self.message_user(request, f"{queryset.count()} messages sent")
