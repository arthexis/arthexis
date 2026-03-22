from django.core.management.base import BaseCommand

from apps.reports.services import run_due_scheduled_reports


class Command(BaseCommand):
    help = "Run due scheduled SQL reports and generate products."

    def handle(self, *args, **options):
        processed = run_due_scheduled_reports()
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} scheduled SQL report(s)."))
