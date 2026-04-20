from django.contrib import admin
from django.urls import reverse

from .models import GalleryCategory, GalleryCredit, GalleryImage, GalleryImageTrait, GalleryTrait


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
        "include_in_public_gallery",
        "content_sample",
        "owner_user",
        "owner_group",
    )
    list_filter = ("include_in_public_gallery", "categories")
    search_fields = ("title", "description")
    filter_horizontal = ("categories",)
    inlines = (GalleryCreditInline, GalleryImageTraitInline)
    change_form_template = "admin/gallery/galleryimage/change_form.html"

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        context = {"uploader_url": reverse("admin:content_contentsample_changelist")}
        if extra_context:
            context.update(extra_context)
        return super().changeform_view(
            request,
            object_id=object_id,
            form_url=form_url,
            extra_context=context,
        )


@admin.register(GalleryCategory)
class GalleryCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")


@admin.register(GalleryTrait)
class GalleryTraitAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")
