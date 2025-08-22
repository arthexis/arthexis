from django.contrib import admin
from django.contrib import admin

from .models import Article, AuthorProfile, MediaImage


class MediaImageInline(admin.TabularInline):
    model = MediaImage
    extra = 0


class ArticleAdmin(admin.ModelAdmin):
    inlines = [MediaImageInline]
    prepopulated_fields = {"slug": ("title",)}

    class Media:
        js = [
            "https://cdn.jsdelivr.net/npm/easymde/dist/easymde.min.js",
            "arts/article_admin.js",
        ]
        css = {"all": ["https://cdn.jsdelivr.net/npm/easymde/dist/easymde.min.css"]}


admin.site.register(Article, ArticleAdmin)
admin.site.register(AuthorProfile)
