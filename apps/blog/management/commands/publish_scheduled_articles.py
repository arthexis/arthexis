from django.core.management.base import BaseCommand

from apps.blog.models import BlogArticle


class Command(BaseCommand):
    help = "Publish due scheduled development blog articles."

    def handle(self, *args, **options):
        del args, options
        result = BlogArticle.publish_ready_articles()
        self.stdout.write(self.style.SUCCESS(f"Published {result.published_count} scheduled article(s)."))
