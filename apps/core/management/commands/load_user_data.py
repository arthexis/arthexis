from django.core.management.commands.loaddata import Command as DjangoLoadDataCommand


class Command(DjangoLoadDataCommand):
    """Load user fixtures."""

    def handle(self, *fixture_labels, **options):
        return super().handle(*fixture_labels, **options)
