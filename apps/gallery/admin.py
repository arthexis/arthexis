from django.contrib import admin
from django.utils import timezone

from .models import (
    GalleryCategory,
    GalleryCredit,
    GalleryImage,
    GalleryImageReaction,
    GalleryImageTrait,
    GalleryTrait,
)


class GalleryCreditInline(admin.TabularInline):
    model = GalleryCredit
    extra = 0


class GalleryImageTraitInline(admin.TabularInline):
    model = GalleryImageTrait
    extra = 0


@admin.register(GalleryImage)
class GalleryImageAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "public_status",
        "public_release_at",
        "content_sample",
        "owner_user",
        "owner_group",
        "guest_key",
    )
    list_filter = ("public_release_at", "categories")
    search_fields = ("title", "description", "guest_key")
    filter_horizontal = ("categories",)
    inlines = (GalleryCreditInline, GalleryImageTraitInline)
    change_form_template = "admin/gallery/galleryimage/change_form.html"

    @admin.display(boolean=True, description="Public now")
    def public_status(self, obj):
        return obj.is_publicly_visible(now=timezone.now())


@admin.register(GalleryCategory)
class GalleryCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")


@admin.register(GalleryTrait)
class GalleryTraitAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")


@admin.register(GalleryImageReaction)
class GalleryImageReactionAdmin(admin.ModelAdmin):
    list_display = ("image", "guest_key", "value", "updated_at")
    list_filter = ("value",)
    search_fields = ("image__title", "guest_key")
