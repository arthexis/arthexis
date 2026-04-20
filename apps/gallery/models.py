import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.content.models import ContentSample
from apps.core.entity import Entity
from apps.groups.models import SecurityGroup
from apps.media.models import MediaFile

from .constants import GALLERY_MANAGER_GROUP_NAME
from .permissions import can_manage_gallery


class GalleryCategory(Entity):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("name",)
        verbose_name = _("Gallery Category")
        verbose_name_plural = _("Gallery Categories")

    def __str__(self) -> str:
        return self.name


class GalleryTrait(Entity):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("name",)
        verbose_name = _("Gallery Trait")
        verbose_name_plural = _("Gallery Traits")

    def __str__(self) -> str:
        return self.name


class GalleryImage(Entity):
    slug = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    media_file = models.ForeignKey(MediaFile, on_delete=models.PROTECT, related_name="gallery_images")
    content_sample = models.ForeignKey(
        ContentSample,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gallery_images",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    include_in_public_gallery = models.BooleanField(default=False)
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_gallery_images",
    )
    owner_group = models.ForeignKey(
        SecurityGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_gallery_images",
    )
    shared_with_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="shared_gallery_images",
    )
    categories = models.ManyToManyField(GalleryCategory, blank=True, related_name="images")
    traits = models.ManyToManyField(GalleryTrait, through="GalleryImageTrait", related_name="images")

    class Meta:
        verbose_name = _("Gallery Image")
        verbose_name_plural = _("Gallery Images")
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(owner_user__isnull=False) & Q(owner_group__isnull=True))
                    | (Q(owner_user__isnull=True) & Q(owner_group__isnull=False))
                ),
                name="gallery_image_single_owner",
            )
        ]

    def __str__(self) -> str:
        return self.title

    def can_view(self, user) -> bool:
        if self.include_in_public_gallery:
            return True
        if not getattr(user, "is_authenticated", False):
            return False
        if can_manage_gallery(user):
            return True
        if self.owner_user_id and self.owner_user_id == user.pk:
            return True
        if self.owner_group_id and user.groups.filter(pk=self.owner_group_id).exists():
            return True
        prefetched_shared_users = getattr(self, "_prefetched_objects_cache", {}).get("shared_with_users")
        if prefetched_shared_users is not None:
            if any(shared_user.pk == user.pk for shared_user in prefetched_shared_users):
                return True
        elif self.shared_with_users.filter(pk=user.pk).exists():
            return True
        return False

    def can_view_metadata(self, user) -> bool:
        if not getattr(user, "is_authenticated", False):
            return False
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            return True
        if self.owner_user_id and self.owner_user_id == user.pk:
            return True
        if self.owner_group_id and user.groups.filter(pk=self.owner_group_id).exists():
            return True
        return user.groups.filter(name=GALLERY_MANAGER_GROUP_NAME).exists()

    def can_share(self, user) -> bool:
        if not getattr(user, "is_authenticated", False):
            return False
        if can_manage_gallery(user):
            return True
        if self.owner_user_id and self.owner_user_id == user.pk:
            return True
        if self.owner_group_id and user.groups.filter(pk=self.owner_group_id).exists():
            return True
        return False


class GalleryCredit(Entity):
    image = models.ForeignKey(GalleryImage, on_delete=models.CASCADE, related_name="credits")
    display_name = models.CharField(max_length=255)
    artist = models.CharField(max_length=255, blank=True, default="")
    series = models.CharField(max_length=255, blank=True, default="")
    era = models.CharField(max_length=255, blank=True, default="")
    apa_citation = models.TextField(blank=True, default="")
    contributed_elements = models.TextField(blank=True, default="")
    excluded_elements = models.TextField(blank=True, default="")
    link_url = models.URLField(blank=True, default="")

    class Meta:
        ordering = ("id",)
        verbose_name = _("Gallery Credit")
        verbose_name_plural = _("Gallery Credits")

    def __str__(self) -> str:
        return self.display_name


class GalleryImageTrait(Entity):
    image = models.ForeignKey(GalleryImage, on_delete=models.CASCADE, related_name="trait_values")
    category = models.ForeignKey(
        GalleryCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trait_values",
    )
    trait = models.ForeignKey(GalleryTrait, on_delete=models.CASCADE, related_name="trait_values")
    qualitative_value = models.CharField(max_length=120, blank=True, default="")
    float_value = models.FloatField(default=1.0)

    class Meta:
        verbose_name = _("Gallery Image Trait")
        verbose_name_plural = _("Gallery Image Traits")
        constraints = [
            models.UniqueConstraint(
                condition=Q(category__isnull=True),
                fields=("image", "trait", "qualitative_value"),
                name="gallery_image_trait_unique_assignment_without_category",
            ),
            models.UniqueConstraint(
                condition=Q(category__isnull=False),
                fields=("image", "category", "trait", "qualitative_value"),
                name="gallery_image_trait_unique_assignment_with_category",
            )
        ]

    def __str__(self) -> str:
        if self.qualitative_value:
            return f"{self.trait}={self.qualitative_value}"
        return str(self.trait)
