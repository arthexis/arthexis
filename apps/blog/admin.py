"""Admin registrations for blog entities."""

from django.contrib import admin

from apps.blog.models import BlogCategory, BlogComment, BlogPost, BlogPostRevision, BlogSeries, BlogTag


@admin.register(BlogCategory)
class BlogCategoryAdmin(admin.ModelAdmin):
    """Admin options for categories."""

    list_display = ("name", "slug")
    search_fields = ("name", "description")


@admin.register(BlogTag)
class BlogTagAdmin(admin.ModelAdmin):
    """Admin options for tags."""

    list_display = ("name", "slug")
    search_fields = ("name",)


class BlogPostRevisionInline(admin.TabularInline):
    """Read-only revision display inline within post admin."""

    model = BlogPostRevision
    extra = 0
    readonly_fields = ("title", "summary", "body", "editor", "note", "created_at")


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    """Admin options for blog posts."""

    list_display = ("title", "author", "status", "published_at", "is_featured")
    list_filter = ("status", "is_featured", "category", "tags")
    search_fields = ("title", "subtitle", "summary", "body")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("author", "category", "series", "tags")
    inlines = [BlogPostRevisionInline]


@admin.register(BlogSeries)
class BlogSeriesAdmin(admin.ModelAdmin):
    """Admin options for series."""

    list_display = ("title", "slug")
    search_fields = ("title", "summary")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(BlogComment)
class BlogCommentAdmin(admin.ModelAdmin):
    """Admin options for comment moderation."""

    list_display = ("post", "author_name", "author_email", "is_approved", "created_at")
    list_filter = ("is_approved", "created_at")
    search_fields = ("author_name", "author_email", "body")
