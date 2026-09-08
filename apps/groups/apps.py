from django.apps import AppConfig


def _ensure_default_staff_groups(sender, user, **kwargs):
    """Apply groups-owned defaults after the base user manager provisions a superuser."""
    from .security import ensure_default_staff_groups

    ensure_default_staff_groups(user)


class GroupsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.groups"
    verbose_name = "Groups"

    def ready(self):
        from apps.base.models import superuser_provisioned

        superuser_provisioned.connect(
            _ensure_default_staff_groups,
            dispatch_uid="apps.groups.ensure_default_staff_groups",
        )
