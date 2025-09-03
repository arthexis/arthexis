from django_celery_beat.apps import BeatConfig as BaseBeatConfig


class HorologiaConfig(BaseBeatConfig):
    """Customize Periodic Tasks app label."""

    verbose_name = "5. Horologia"
