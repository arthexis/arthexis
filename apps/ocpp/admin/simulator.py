from .common_imports import *
from .common import LogViewAdminMixin
from django.core.exceptions import PermissionDenied

from ..cpsim_service import (
    cpsim_service_enabled,
    get_cpsim_feature,
    queue_cpsim_request,
    queue_cpsim_service_toggle,
)

class SimulatorAdmin(SaveBeforeChangeAction, LogViewAdminMixin, EntityModelAdmin):
    change_list_template = "admin/ocpp/simulator/change_list.html"
    list_display = (
        "name",
        "default",
        "host",
        "ws_port",
        "ws_url",
        "interval",
        "average_kwh_display",
        "amperage",
        "running",
        "log_link",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "default",
                    "name",
                    "cp_path",
                    ("host", "ws_port"),
                    "rfid",
                    ("duration", "interval", "pre_charge_delay"),
                    ("average_kwh", "amperage"),
                    ("repeat", "door_open"),
                    ("username", "password"),
                )
            },
        ),
        (
            "Configuration",
            {
                "fields": ("configuration",),
                "classes": ("collapse",),
                "description": (
                    "Select a CP Configuration to reuse for GetConfiguration responses."
                ),
            },
        ),
    )
    actions = (
        "start_simulator",
        "stop_simulator",
        "send_open_door",
    )
    changelist_actions = ["start_default"]
    change_actions = ["start_action", "stop_action"]

    log_type = "simulator"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "cpsim-toggle/",
                self.admin_site.admin_view(self.toggle_cpsim_service),
                name="ocpp_simulator_cpsim_toggle",
            ),
        ]
        return custom + urls

    def _cpsim_context(self):
        feature = get_cpsim_feature()
        enabled = bool(feature and feature.is_enabled)
        return {
            "cpsim_feature": feature,
            "cpsim_service_enabled": enabled,
            "cpsim_service_status": _("Enabled") if enabled else _("Disabled"),
            "cpsim_toggle_label": _("Disable") if enabled else _("Enable"),
            "cpsim_toggle_url": reverse("admin:ocpp_simulator_cpsim_toggle"),
        }

    def changelist_view(self, request, extra_context=None):
        context = extra_context or {}
        context.update(self._cpsim_context())
        return super().changelist_view(request, extra_context=context)

    def toggle_cpsim_service(self, request):
        if request.method != "POST":
            raise PermissionDenied
        if not self.has_change_permission(request):
            raise PermissionDenied
        node = Node.get_local()
        if node is None:
            self.message_user(
                request,
                "No local node is registered; unable to toggle the CP simulator service.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:ocpp_simulator_changelist"))
        feature = get_cpsim_feature()
        if not feature:
            self.message_user(
                request,
                "CP simulator service feature is not configured.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:ocpp_simulator_changelist"))
        current = set(
            node.features.filter(slug__in=Node.MANUAL_FEATURE_SLUGS).values_list(
                "slug", flat=True
            )
        )
        service_enabled = not feature.is_enabled
        if service_enabled:
            current.add(feature.slug)
            action = "enabled"
        else:
            current.discard(feature.slug)
            action = "disabled"
        node.update_manual_features(current)
        queue_cpsim_service_toggle(enabled=service_enabled, source="admin")
        self.message_user(
            request,
            f"{feature.display} {action} for this node.",
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:ocpp_simulator_changelist"))

    @admin.display(description="Average kWh", ordering="average_kwh")
    def average_kwh_display(self, obj):
        """Display ``average_kwh`` with a dot decimal separator for Spanish locales."""

        language = translation.get_language() or ""
        if language.startswith("es"):
            return formats.number_format(
                obj.average_kwh,
                decimal_pos=2,
                use_l10n=False,
                force_grouping=False,
            )

        return formats.number_format(
            obj.average_kwh,
            decimal_pos=2,
            use_l10n=True,
            force_grouping=False,
        )

    def save_model(self, request, obj, form, change):
        previous_door_open = False
        if change and obj.pk:
            previous_door_open = (
                type(obj)
                .objects.filter(pk=obj.pk)
                .values_list("door_open", flat=True)
                .first()
                or False
            )
        super().save_model(request, obj, form, change)
        if obj.door_open and not previous_door_open:
            triggered = self._queue_door_open(request, obj)
            if not triggered:
                type(obj).objects.filter(pk=obj.pk).update(door_open=False)
                obj.door_open = False

    def _queue_door_open(self, request, obj) -> bool:
        sim = store.simulators.get(obj.pk)
        if not sim:
            self.message_user(
                request,
                f"{obj.name}: simulator is not running",
                level=messages.ERROR,
            )
            return False
        type(obj).objects.filter(pk=obj.pk).update(door_open=True)
        obj.door_open = True
        store.add_log(
            obj.cp_path,
            "Door open event requested from admin",
            log_type="simulator",
        )
        if hasattr(sim, "trigger_door_open"):
            sim.trigger_door_open()
        else:  # pragma: no cover - unexpected condition
            self.message_user(
                request,
                f"{obj.name}: simulator cannot send door open event",
                level=messages.ERROR,
            )
            type(obj).objects.filter(pk=obj.pk).update(door_open=False)
            obj.door_open = False
            return False
        type(obj).objects.filter(pk=obj.pk).update(door_open=False)
        obj.door_open = False
        self.message_user(
            request,
            f"{obj.name}: DoorOpen status notification sent",
        )
        return True

    def running(self, obj):
        return obj.pk in store.simulators

    running.boolean = True

    @admin.action(description="Send Open Door")
    def send_open_door(self, request, queryset):
        for obj in queryset:
            self._queue_door_open(request, obj)

    def _start_simulators(self, request, queryset):
        from django.urls import reverse
        from django.utils.html import format_html

        for obj in queryset:
            if obj.pk in store.simulators:
                self.message_user(request, f"{obj.name}: already running")
                continue
            type(obj).objects.filter(pk=obj.pk).update(door_open=False)
            obj.door_open = False
            store.register_log_name(obj.cp_path, obj.name, log_type="simulator")
            if cpsim_service_enabled():
                queue_cpsim_request(
                    action="start",
                    params=obj.as_config(),
                    simulator_id=obj.pk,
                    name=obj.name,
                    source="admin",
                )
                started, status = True, "cpsim-service start requested"
                log_file = str(store._file_path(obj.cp_path, log_type="simulator"))
            else:
                sim = ChargePointSimulator(obj.as_config())
                started, status, log_file = sim.start()
                if started:
                    store.simulators[obj.pk] = sim
            log_url = reverse("admin:ocpp_simulator_log", args=[obj.pk])
            self.message_user(
                request,
                format_html(
                    '{}: {}. Log: <code>{}</code> (<a href="{}" target="_blank">View Log</a>)',
                    obj.name,
                    status,
                    log_file,
                    log_url,
                ),
            )

    @admin.action(description="Start selected simulators")
    def start_simulator(self, request, queryset):
        self._start_simulators(request, queryset)

    @admin.action(description="Start Default")
    def start_default(self, request, queryset=None):
        from django.urls import reverse
        from django.utils.html import format_html

        default_simulator = (
            Simulator.objects.filter(default=True, is_deleted=False).order_by("pk").first()
        )
        if default_simulator is None:
            self.message_user(
                request,
                "No default simulator is configured.",
                level=messages.ERROR,
            )
        else:
            if default_simulator.pk in store.simulators:
                self.message_user(
                    request,
                    f"{default_simulator.name}: already running",
                )
            else:
                type(default_simulator).objects.filter(pk=default_simulator.pk).update(
                    door_open=False
                )
                default_simulator.door_open = False
                store.register_log_name(
                    default_simulator.cp_path, default_simulator.name, log_type="simulator"
                )
                if cpsim_service_enabled():
                    queue_cpsim_request(
                        action="start",
                        params=default_simulator.as_config(),
                        simulator_id=default_simulator.pk,
                        name=default_simulator.name,
                        source="admin",
                    )
                    started, status = True, "cpsim-service start requested"
                    log_file = str(
                        store._file_path(default_simulator.cp_path, log_type="simulator")
                    )
                else:
                    simulator = ChargePointSimulator(default_simulator.as_config())
                    started, status, log_file = simulator.start()
                    if started:
                        store.simulators[default_simulator.pk] = simulator
                log_url = reverse("admin:ocpp_simulator_log", args=[default_simulator.pk])
                self.message_user(
                    request,
                    format_html(
                        '{}: {}. Log: <code>{}</code> (<a href="{}" target="_blank">View Log</a>)',
                        default_simulator.name,
                        status,
                        log_file,
                    log_url,
                ),
            )

        return HttpResponseRedirect(reverse("admin:ocpp_simulator_changelist"))

    start_default.label = _("Start Default")
    start_default.requires_queryset = False

    def stop_simulator(self, request, queryset):
        async def _stop(objs):
            for obj in objs:
                if cpsim_service_enabled():
                    queue_cpsim_request(
                        action="stop",
                        params=obj.as_config(),
                        simulator_id=obj.pk,
                        name=obj.name,
                        source="admin",
                    )
                else:
                    sim = store.simulators.pop(obj.pk, None)
                    if sim:
                        await sim.stop()

        objs = list(queryset)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_stop(objs))
        else:
            loop.create_task(_stop(objs))
        self.message_user(request, "Stopping simulators")

    stop_simulator.short_description = "Stop selected simulators"

    def start_action(self, request, obj):
        queryset = type(obj).objects.filter(pk=obj.pk)
        self.start_simulator(request, queryset)

    def stop_action(self, request, obj):
        queryset = type(obj).objects.filter(pk=obj.pk)
        self.stop_simulator(request, queryset)

    def response_action(self, request, queryset):
        if request.POST.get("action") == "start_default":
            return self.start_default(request)
        return super().response_action(request, queryset)

    def log_link(self, obj):
        from django.utils.html import format_html
        from django.urls import reverse

        url = reverse("admin:ocpp_simulator_log", args=[obj.pk])
        return format_html('<a href="{}" target="_blank">view</a>', url)

    log_link.short_description = "Log"

    def get_log_identifier(self, obj):
        return obj.cp_path
