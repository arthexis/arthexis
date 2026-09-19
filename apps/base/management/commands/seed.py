from django.core.management.base import BaseCommand

from apps.base.models import SchemaGeneration


class Command(BaseCommand):
    help = "Ensures the Arthexis 2.0 schema-generation marker exists."

    def handle(self, *args, **options):
        SchemaGeneration.objects.update_or_create(generation=2)
        self.stdout.write(self.style.SUCCESS("Arthexis 2.0 foundation is ready."))
