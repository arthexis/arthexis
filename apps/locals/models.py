from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import connections, models, router
from django.db.utils import OperationalError, ProgrammingError
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class Favorite(Entity):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorites",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    custom_label = models.CharField(max_length=100, blank=True)
    user_data = models.BooleanField(default=False)
    priority = models.IntegerField(default=0)

    class Meta:
        db_table = "pages_favorite"
        unique_together = ("user", "content_type")
        ordering = ["priority", "pk"]
        verbose_name = _("Favorite")
        verbose_name_plural = _("Favorites")


def ensure_admin_favorites(user) -> None:
    """Ensure the default admin account has standard favorites configured."""
    if not user:
        return

    db_alias = router.db_for_write(Favorite, instance=user)
    connection = connections[db_alias]
    try:
        if Favorite._meta.db_table not in connection.introspection.table_names():
            return
    except (OperationalError, ProgrammingError):
        return

    model_targets = (
        ("nodes", "Node"),
        ("cards", "RFID"),
        ("ocpp", "Simulator"),
        ("nginx", "SiteConfiguration"),
        ("ocpp", "Charger"),
        ("release", "PackageRelease"),
    )
    content_types = []
    for app_label, model_name in model_targets:
        try:
            model = django_apps.get_model(app_label, model_name)
        except LookupError:
            continue
        try:
            content_types.append(ContentType.objects.get_for_model(model))
        except (OperationalError, ProgrammingError):
            return

    if not content_types:
        return

    try:
        existing = set(
            Favorite.objects.filter(
                user=user, content_type__in=content_types
            ).values_list(
                "content_type_id",
                flat=True,
            )
        )
    except (OperationalError, ProgrammingError):
        return
    new_favorites = []
    for priority, content_type in enumerate(content_types):
        if content_type.pk in existing:
            continue
        new_favorites.append(
            Favorite(
                user=user,
                content_type=content_type,
                user_data=True,
                priority=priority,
            )
        )
    if new_favorites:
        Favorite.objects.bulk_create(new_favorites)
        from .favorites_cache import clear_user_favorites_cache

        clear_user_favorites_cache(user)
