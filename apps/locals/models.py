from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import connections, models, router
from django.db.utils import OperationalError, ProgrammingError
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.groups.constants import PRODUCT_DEVELOPER_GROUP_NAME, SITE_OPERATOR_GROUP_NAME

PRODUCT_DEVELOPER_FAVORITE_TARGETS: tuple[tuple[str, str], ...] = (
    ("app", "Application"),
    ("release", "PackageRelease"),
    ("repos", "RepositoryIssue"),
    ("tests", "SuiteTest"),
)
SITE_OPERATOR_FAVORITE_TARGETS: tuple[tuple[str, str], ...] = (
    ("cards", "RFIDAttempt"),
    ("django_celery_beat", "PeriodicTask"),
)


class Favorite(Entity):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorites",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    custom_label = models.CharField(max_length=100, blank=True)
    user_data = models.BooleanField(default=True)
    priority = models.IntegerField(default=0)

    class Meta:
        db_table = "pages_favorite"
        unique_together = ("user", "content_type")
        ordering = ["priority", "pk"]
        verbose_name = _("Favorite")
        verbose_name_plural = _("Favorites")

    def save(self, *args, **kwargs):
        """Persist favorites as user data for both create and update paths."""
        is_new = self.pk is None
        self.user_data = True
        self.is_user_data = True
        super().save(*args, **kwargs)
        if not is_new and (not self.user_data or not self.is_user_data):
            type(self).all_objects.filter(pk=self.pk).update(
                user_data=True,
                is_user_data=True,
            )
            self.user_data = True
            self.is_user_data = True


def ensure_admin_favorites(user) -> None:
    """Ensure the default admin account has standard favorites configured."""
    ensure_user_favorites(
        user,
        model_targets=(
            ("cards", "RFID"),
            ("links", "Reference"),
            ("ocpp", "Charger"),
        ),
    )


def ensure_security_group_favorites(user) -> None:
    """Ensure users receive default favorites from their security-group roles."""
    if not user:
        return
    if not (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
        return

    try:
        group_names = set(user.groups.values_list("name", flat=True))
    except (OperationalError, ProgrammingError):
        return

    model_targets: list[tuple[str, str]] = []
    if PRODUCT_DEVELOPER_GROUP_NAME in group_names:
        model_targets.extend(PRODUCT_DEVELOPER_FAVORITE_TARGETS)
    if SITE_OPERATOR_GROUP_NAME in group_names:
        model_targets.extend(SITE_OPERATOR_FAVORITE_TARGETS)
    if not model_targets:
        return

    ensure_user_favorites(user, model_targets=tuple(dict.fromkeys(model_targets)))


def ensure_user_favorites(user, model_targets: tuple[tuple[str, str], ...]) -> None:
    """Ensure a user has favorites for each target model in ``model_targets``."""
    if not user:
        return

    db_alias = router.db_for_write(Favorite, instance=user)
    connection = connections[db_alias]
    try:
        if Favorite._meta.db_table not in connection.introspection.table_names():
            return
    except (OperationalError, ProgrammingError):
        return

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
                user=user,
                content_type__in=content_types,
            ).values_list(
                "content_type_id",
                flat=True,
            )
        )
        max_priority = Favorite.objects.filter(user=user).aggregate(
            max_priority=models.Max("priority")
        )["max_priority"]
    except (OperationalError, ProgrammingError):
        return

    next_priority = (max_priority or -1) + 1
    new_favorites = []
    for content_type in content_types:
        if content_type.pk in existing:
            continue
        new_favorites.append(
            Favorite(
                user=user,
                content_type=content_type,
                is_user_data=True,
                user_data=True,
                priority=next_priority,
            )
        )
        next_priority += 1

    if new_favorites:
        Favorite.objects.bulk_create(new_favorites)
        from .favorites_cache import clear_user_favorites_cache

        clear_user_favorites_cache(user)
