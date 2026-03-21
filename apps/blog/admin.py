from django.contrib import admin
from django.urls import reverse

from apps.blog.models import (
    BlogArticle,
    BlogCodeReference,
    BlogRevision,
    BlogSeries,
    BlogSigilShortcut,
    BlogTag,
)
from apps.core.admin.mixins import PublicViewLinksAdminMixin


class BlogCodeReferenceInline(admin.TabularInline):
    model = BlogCodeReference
    extra = 0


class BlogSigilShortcutInline(admin.TabularInline):
    model = BlogSigilShortcut
    extra = 0


class BlogRevisionInline(admin.TabularInline):
    model = BlogRevision
    extra = 0
    readonly_fields = ("title", "body", "change_note", "created_by", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        del request, obj
        return False


@admin.register(BlogArticle)
class BlogArticleAdmin(PublicViewLinksAdminMixin, admin.ModelAdmin):
    """Admin configuration for engineering blog articles."""

    list_display = (
        "title",
        "status",
        "author",
        "series",
        "publish_at",
        "published_at",
        "is_featured",
    )
    list_filter = ("status", "body_format", "series", "is_featured", "allow_comments")
    search_fields = ("title", "subtitle", "excerpt", "body", "slug")
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ("tags", "reviewers")
    inlines = (BlogCodeReferenceInline, BlogSigilShortcutInline, BlogRevisionInline)
    view_on_site = True

    def get_public_view_links(self, obj=None) -> list[dict[str, str]]:
        """Return public blog routes relevant to the current admin page."""

        links = [{"label": "View on site: Blog index", "url": reverse("blog-list")}]
        if obj:
            links.append({"label": "View on site: Article", "url": obj.get_absolute_url()})
        return links


@admin.register(BlogSeries)
class BlogSeriesAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "is_active")
    search_fields = ("title", "description", "slug")


@admin.register(BlogTag)
class BlogTagAdmin(admin.ModelAdmin):
    list_display = ("label", "slug")
    search_fields = ("label", "slug")
