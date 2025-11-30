from django.utils.translation import gettext_lazy as _

from apps.core.models import CountdownTimer


class CeleryCountdownTimer(CountdownTimer):
    class Meta:
        proxy = True
        app_label = "django_celery_beat"
        verbose_name = _("Countdown Timer")
        verbose_name_plural = _("Countdown Timers")
