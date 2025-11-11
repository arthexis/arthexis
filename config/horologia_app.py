from django_celery_beat.apps import BeatConfig as BaseBeatConfig


class HorologiaConfig(BaseBeatConfig):
    """Customize Periodic Tasks admin behavior."""

    verbose_name = "5. Horologia"

    def ready(self):  # pragma: no cover - exercised via tests
        super().ready()

        from django.contrib import admin
        from django.contrib.admin.sites import NotRegistered
        from django.utils import timezone
        from django.utils.translation import gettext_lazy as _

        from django_celery_beat import admin as celery_admin
        from django_celery_beat.models import IntervalSchedule, PeriodicTask

        class HorologiaPeriodicTaskAdmin(celery_admin.PeriodicTaskAdmin):
            """Patch the periodic task changelist."""

            class IntervalTypeListFilter(admin.SimpleListFilter):
                title = _("Interval type")
                parameter_name = "interval__period__exact"

                def lookups(self, request, model_admin):
                    field = IntervalSchedule._meta.get_field("period")
                    return field.flatchoices

                def queryset(self, request, queryset):
                    interval_type = self.value()
                    if interval_type:
                        return queryset.filter(
                            **{self.parameter_name: interval_type}
                        )
                    return queryset

            list_display = (
                "name",
                "enabled",
                "scheduler",
                "interval",
                "last_run",
                "one_off",
            )
            list_filter = (
                "enabled",
                "one_off",
                "start_time",
                "last_run_at",
                IntervalTypeListFilter,
            )

            @admin.display(ordering="last_run_at", description=_("Last run"))
            def last_run(self, obj):
                last_run_at = getattr(obj, "last_run_at", None)
                if last_run_at is None:
                    return ""
                if timezone.is_aware(last_run_at):
                    last_run_at = timezone.localtime(last_run_at)
                return last_run_at.replace(microsecond=0).isoformat()

        try:
            admin.site.unregister(PeriodicTask)
        except NotRegistered:  # pragma: no cover - defensive
            pass
        admin.site.register(PeriodicTask, HorologiaPeriodicTaskAdmin)
