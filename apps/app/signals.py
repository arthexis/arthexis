from django.db.models.signals import post_delete, post_migrate, post_save
from django.dispatch import receiver

from apps.app.models import Application, refresh_application_models, refresh_enabled_apps_lock
from utils.post_migrate import is_final_post_migrate_app


@receiver(post_migrate)
def sync_application_models(sender, app_config, using, **kwargs):
    if not is_final_post_migrate_app(app_config):
        return
    refresh_application_models(using=using)


@receiver(post_save, sender=Application)
def sync_enabled_apps_lock_on_save(sender, instance, using, **kwargs):
    """Update the enabled app lock whenever an Application is saved."""

    refresh_enabled_apps_lock(using=using)


@receiver(post_delete, sender=Application)
def sync_enabled_apps_lock_on_delete(sender, instance, using, **kwargs):
    """Update the enabled app lock whenever an Application is deleted."""

    refresh_enabled_apps_lock(using=using)
