from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _, ngettext

from apps.chats.models import ChatMessage, ChatSession
from apps.locals.user_data import EntityModelAdmin


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    can_delete = False
    extra = 0
    fields = ("created_at", "author", "from_staff", "body")
    readonly_fields = fields
    ordering = ("created_at",)

    @admin.display(description=_("Author"))
    def author(self, obj):
        return obj.author_label()


@admin.register(ChatSession)
class ChatSessionAdmin(EntityModelAdmin):
    date_hierarchy = "created_at"
    list_display = (
        "uuid",
        "site",
        "whatsapp_number",
        "status",
        "last_activity_at",
        "last_visitor_activity_at",
        "last_staff_activity_at",
        "escalated_at",
    )
    list_filter = ("status", "site")
    search_fields = (
        "uuid",
        "visitor_key",
        "whatsapp_number",
        "user__username",
        "messages__body",
    )
    readonly_fields = (
        "uuid",
        "created_at",
        "updated_at",
        "last_activity_at",
        "last_visitor_activity_at",
        "last_staff_activity_at",
        "escalated_at",
        "closed_at",
        "visitor_key",
        "is_seed_data",
        "is_user_data",
        "is_deleted",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "uuid",
                    "status",
                    "site",
                    "user",
                    "visitor_key",
                    "whatsapp_number",
                )
            },
        ),
        (
            _("Activity"),
            {
                "fields": (
                    "created_at",
                    "updated_at",
                    "last_activity_at",
                    "last_visitor_activity_at",
                    "last_staff_activity_at",
                    "escalated_at",
                    "closed_at",
                ),
            },
        ),
        (
            _("Flags"),
            {
                "fields": ("is_seed_data", "is_user_data", "is_deleted"),
                "classes": ("collapse",),
            },
        ),
    )
    inlines = [ChatMessageInline]
    list_select_related = ("site", "user")
    ordering = ("-last_activity_at",)
    actions = ["close_sessions"]

    @admin.action(description=_("Close selected sessions"))
    def close_sessions(self, request, queryset):
        closed = 0
        for session in queryset:
            if session.status != session.Status.CLOSED:
                session.close()
                closed += 1
        if closed:
            self.message_user(
                request,
                ngettext(
                    "Closed %(count)d chat session.",
                    "Closed %(count)d chat sessions.",
                    closed,
                )
                % {"count": closed},
                messages.SUCCESS,
            )
