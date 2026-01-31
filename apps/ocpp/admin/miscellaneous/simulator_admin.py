from ..common_imports import *


class LogViewAdminMixin:
    """Mixin providing an admin view to display charger or simulator logs."""

    log_type = "charger"
    log_template_name = "admin/ocpp/log_view.html"

    def get_log_identifier(self, obj):  # pragma: no cover - mixin hook
        raise NotImplementedError

    def get_log_title(self, obj):
        return f"Log for {obj}"

    def get_urls(self):
        urls = super().get_urls()
        info = self.model._meta.app_label, self.model._meta.model_name
        custom = [
            path(
                "<path:object_id>/log/",
                self.admin_site.admin_view(self.log_view),
                name=f"{info[0]}_{info[1]}_log",
            ),
        ]
        return custom + urls

    def log_view(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            info = self.model._meta.app_label, self.model._meta.model_name
            changelist_url = reverse(
                "admin:%s_%s_changelist" % info,
                current_app=self.admin_site.name,
            )
            self.message_user(request, "Log is not available.", messages.ERROR)
            return redirect(changelist_url)
        identifier = self.get_log_identifier(obj)
        log_entries = store.get_logs(identifier, log_type=self.log_type)
        log_file = store._file_path(identifier, log_type=self.log_type)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": obj,
            "title": self.get_log_title(obj),
            "log_entries": log_entries,
            "log_file": str(log_file),
            "log_identifier": identifier,
        }
        return TemplateResponse(request, self.log_template_name, context)


@admin.register(Simulator)
class SimulatorAdmin(SaveBeforeChangeAction, LogViewAdminMixin, EntityModelAdmin):
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
        for obj in queryset:
            if obj.pk in store.simulators:
                self.message_user(request, f"{obj.name}: already running")
                continue
            type(obj).objects.filter(pk=obj.pk).update(door_open=False)
            obj.door_open = False
            store.register_log_name(obj.cp_path, obj.name, log_type="simulator")
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

    @admin.action(description=_("Start Default"))
    def start_default(self, request, queryset=None):
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
        url = reverse("admin:ocpp_simulator_log", args=[obj.pk])
        return format_html('<a href="{}" target="_blank">view</a>', url)

    log_link.short_description = "Log"

    def get_log_identifier(self, obj):
        return obj.cp_path
