from __future__ import annotations

from django.views.generic import ListView
from packaging.version import Version

from core.models import NewsArticle


class NewsArticleListView(ListView):
    model = NewsArticle
    template_name = "news/list.html"
    paginate_by = 4

    def get_queryset(self):
        qs = super().get_queryset().order_by("-published")
        year = self.request.GET.get("year")
        month = self.request.GET.get("month")
        if year and month:
            try:
                qs = qs.filter(published__year=int(year), published__month=int(month))
            except ValueError:
                pass
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        months = NewsArticle.objects.dates("published", "month", order="DESC")[:3]
        context["months"] = months
        all_articles = list(NewsArticle.objects.all())
        all_articles.sort(
            key=lambda a: Version(a.name.split()[0]),
            reverse=True,
        )
        context["all_articles"] = all_articles
        return context
