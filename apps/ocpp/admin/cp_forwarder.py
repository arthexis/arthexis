from .common_imports import *

class CPForwarderForm(forms.ModelForm):
    forwarded_messages = forms.MultipleChoiceField(
        label=_("Forwarded messages"),
        choices=[
            (message, message)
            for message in CPForwarder.available_forwarded_messages()
        ],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text=_(
            "Choose which OCPP messages should be forwarded. Only charge points "
            "with Export transactions enabled are eligible."
        ),
    )

    class Meta:
        model = CPForwarder
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial = CPForwarder.available_forwarded_messages()
        if self.instance and self.instance.pk:
            initial = self.instance.get_forwarded_messages()
        self.fields["forwarded_messages"].initial = initial

    def clean_forwarded_messages(self):
        selected = self.cleaned_data.get("forwarded_messages") or []
        return CPForwarder.sanitize_forwarded_messages(selected)

class CPForwarderAdmin(EntityModelAdmin):
    form = CPForwarderForm
    list_display = (
        "display_name",
        "target_node",
        "enabled",
        "is_running",
        "last_forwarded_at",
        "last_status",
        "last_error",
    )
    list_filter = ("enabled", "is_running", "target_node")
    search_fields = (
        "name",
        "target_node__hostname",
        "target_node__public_endpoint",
        "target_node__address",
    )
    autocomplete_fields = ["target_node", "source_node"]
    readonly_fields = (
        "is_running",
        "last_forwarded_at",
        "last_status",
        "last_error",
        "last_synced_at",
        "created_at",
        "updated_at",
    )
    actions = [
        "enable_forwarders",
        "disable_forwarders",
        "enable_export_transactions",
        "disable_export_transactions",
        "test_forwarders",
    ]

    fieldsets = (
        (
            None,
            {
                "description": _(
                    "Only charge points with Export transactions enabled will be "
                    "forwarded by this configuration."
                ),
                "fields": (
                    "name",
                    "source_node",
                    "target_node",
                    "enabled",
                    "is_running",
                    "last_forwarded_at",
                    "last_status",
                    "last_error",
                    "last_synced_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
        (
            _("Forwarding"),
            {
                "classes": ("collapse",),
                "fields": ("forwarded_messages",),
            },
        ),
    )

    @admin.display(description=_("Name"))
    def display_name(self, obj):
        if obj.name:
            return obj.name
        if obj.target_node:
            return str(obj.target_node)
        return _("Forwarder")

    def _chargers_for_forwarder(self, forwarder):
        from apps.ocpp.models import Charger

        queryset = Charger.objects.all()
        source_node = forwarder.source_node or Node.get_local()
        if source_node and source_node.pk:
            queryset = queryset.filter(
                Q(node_origin=source_node) | Q(node_origin__isnull=True)
            )
        return queryset

    def _toggle_forwarders(self, request, queryset, enabled: bool) -> None:
        if not queryset.exists():
            self.message_user(
                request,
                _("No forwarders were selected."),
                messages.WARNING,
            )
            return
        queryset.update(enabled=enabled)
        synced = 0
        failed = 0
        for forwarder in queryset:
            try:
                forwarder.sync_chargers()
                synced += 1
            except Exception as exc:
                failed += 1
                self.message_user(
                    request,
                    _("Failed to sync forwarder %(name)s: %(error)s")
                    % {"name": forwarder, "error": exc},
                    messages.ERROR,
                )
        if synced:
            self.message_user(
                request,
                _("Updated %(count)s forwarder(s).") % {"count": synced},
                messages.SUCCESS,
            )
        if failed:
            self.message_user(
                request,
                _("Failed to update %(count)s forwarder(s).") % {"count": failed},
                messages.ERROR,
            )

    def _toggle_export_transactions(self, request, queryset, enabled: bool) -> None:
        if not queryset.exists():
            self.message_user(
                request,
                _("No forwarders were selected."),
                messages.WARNING,
            )
            return
        updated = 0
        for forwarder in queryset:
            chargers = self._chargers_for_forwarder(forwarder)
            updated += chargers.update(export_transactions=enabled)
            try:
                forwarder.sync_chargers()
            except Exception as exc:
                self.message_user(
                    request,
                    _("Failed to sync forwarder %(name)s: %(error)s")
                    % {"name": forwarder, "error": exc},
                    messages.ERROR,
                )
        self.message_user(
            request,
            _("Updated export settings for %(count)s charge point(s).")
            % {"count": updated},
            messages.SUCCESS,
        )

    @admin.action(description=_("Enable selected forwarders"))
    def enable_forwarders(self, request, queryset):
        self._toggle_forwarders(request, queryset, True)

    @admin.action(description=_("Disable selected forwarders"))
    def disable_forwarders(self, request, queryset):
        self._toggle_forwarders(request, queryset, False)

    @admin.action(description=_("Enable export transactions for charge points"))
    def enable_export_transactions(self, request, queryset):
        self._toggle_export_transactions(request, queryset, True)

    @admin.action(description=_("Disable export transactions for charge points"))
    def disable_export_transactions(self, request, queryset):
        self._toggle_export_transactions(request, queryset, False)

    @admin.action(description=_("Test forwarder configuration"))
    def test_forwarders(self, request, queryset):
        tested = 0
        for forwarder in queryset:
            forwarder.sync_chargers()
            tested += 1
        if tested:
            self.message_user(
                request,
                _("Tested %(count)s forwarder(s).") % {"count": tested},
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("No forwarders were selected."),
                messages.WARNING,
            )
