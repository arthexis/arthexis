from django.core.management.base import BaseCommand

from apps.publish.blog.models import BlogArticle


class Command(BaseCommand):
    help = "Publish due scheduled development blog articles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview articles that would be published without committing changes.",
        )

    def handle(self, *args, **options):
        del args
        verbosity = int(options.get("verbosity", 1))
        dry_run = bool(options.get("dry_run", False))

        if dry_run:
            count = BlogArticle.objects.ready_to_publish().count()
            if verbosity >= 1:
                self.stdout.write(f"[dry-run] Would publish {count} scheduled article(s).")
            return

        result = BlogArticle.publish_ready_articles()
        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS(f"Published {result.published_count} scheduled article(s)."))
