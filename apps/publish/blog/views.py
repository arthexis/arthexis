from django.views.generic import DetailView, ListView

from apps.publish.blog.models import BlogArticle


class BlogArticleListView(ListView):
    """Public list view of published engineering blog posts."""

    template_name = "blog/article_list.html"
    context_object_name = "articles"

    def get_queryset(self):
        return (
            BlogArticle.objects.published()
            .select_related("author", "series")
            .prefetch_related("tags")
        )


class BlogArticleDetailView(DetailView):
    """Public detail view for published engineering blog posts."""

    template_name = "blog/article_detail.html"
    context_object_name = "article"
    slug_field = "slug"

    def get_queryset(self):
        return (
            BlogArticle.objects.published()
            .select_related("author", "series")
            .prefetch_related("tags", "code_references", "sigil_shortcuts")
        )
