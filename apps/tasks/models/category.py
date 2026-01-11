from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityAllManager, EntityManager
from apps.media.models import MediaFile
from apps.media.utils import ensure_media_bucket
from apps.odoo.models import OdooProduct as CoreOdooProduct

from .constants import (
    AVAILABILITY_2_3_BUSINESS_DAYS,
    AVAILABILITY_2_3_WEEKS,
    AVAILABILITY_CHOICES,
    AVAILABILITY_IMMEDIATE,
    AVAILABILITY_NONE,
    AVAILABILITY_UNAVAILABLE,
)


class TaskCategoryManager(EntityManager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class TaskCategory(Entity):
    """Standardized categories for manual work assignments."""

    AVAILABILITY_NONE = AVAILABILITY_NONE
    AVAILABILITY_IMMEDIATE = AVAILABILITY_IMMEDIATE
    AVAILABILITY_2_3_BUSINESS_DAYS = AVAILABILITY_2_3_BUSINESS_DAYS
    AVAILABILITY_2_3_WEEKS = AVAILABILITY_2_3_WEEKS
    AVAILABILITY_UNAVAILABLE = AVAILABILITY_UNAVAILABLE

    AVAILABILITY_CHOICES = AVAILABILITY_CHOICES

    name = models.CharField(_("Name"), max_length=200)
    description = models.TextField(
        _("Description"),
        blank=True,
        help_text=_("Optional details supporting Markdown formatting."),
    )
    image_media = models.ForeignKey(
        MediaFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_category_images",
        verbose_name=_("Image"),
    )
    cost = models.DecimalField(
        _("Cost"),
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=_("Estimated fulfillment cost in local currency."),
    )
    default_duration = models.DurationField(
        _("Default duration"),
        null=True,
        blank=True,
        help_text=_("Typical time expected to complete tasks in this category."),
    )
    manager = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_task_categories",
        verbose_name=_("Manager"),
        help_text=_("User responsible for overseeing this category."),
    )
    odoo_products = models.ManyToManyField(
        CoreOdooProduct,
        related_name="task_categories",
        verbose_name=_("Odoo products"),
        blank=True,
        help_text=_("Relevant Odoo products for this category."),
    )
    availability = models.CharField(
        _("Availability"),
        max_length=32,
        choices=AVAILABILITY_CHOICES,
        default=AVAILABILITY_NONE,
        help_text=_("Typical lead time for scheduling this work."),
    )
    requestor_group = models.ForeignKey(
        "groups.SecurityGroup",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="task_categories_as_requestor",
        verbose_name=_("Requestor group"),
        help_text=_("Security group authorized to request this work."),
    )
    assigned_group = models.ForeignKey(
        "groups.SecurityGroup",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="task_categories_as_assignee",
        verbose_name=_("Assigned to group"),
        help_text=_("Security group typically assigned to this work."),
    )

    objects = TaskCategoryManager()
    all_objects = EntityAllManager()

    class Meta:
        verbose_name = _("Task Category")
        verbose_name_plural = _("Task Categories")
        ordering = ("name",)
        db_table = "core_taskcategory"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    natural_key.dependencies = []  # type: ignore[attr-defined]

    def availability_label(self) -> str:  # pragma: no cover - admin helper
        return self.get_availability_display()

    availability_label.short_description = _("Availability")  # type: ignore[attr-defined]

    @property
    def image_file(self):
        if self.image_media and self.image_media.file:
            return self.image_media.file
        return None


TASK_CATEGORY_BUCKET_SLUG = "tasks-category-images"
TASK_CATEGORY_ALLOWED_PATTERNS = "\n".join(["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp"])


def get_task_category_bucket():
    return ensure_media_bucket(
        slug=TASK_CATEGORY_BUCKET_SLUG,
        name=_("Task Category Images"),
        allowed_patterns=TASK_CATEGORY_ALLOWED_PATTERNS,
        max_bytes=2 * 1024 * 1024,
        expires_at=None,
    )
