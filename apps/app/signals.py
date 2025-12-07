from django.db.models.signals import post_migrate
from django.dispatch import receiver

from apps.app.models import refresh_application_models


@receiver(post_migrate)
def sync_application_models(sender, app_config, using, **kwargs):
    refresh_application_models(using=using)
