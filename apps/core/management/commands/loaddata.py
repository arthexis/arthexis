import logging

from django.core.management.commands.loaddata import Command as DjangoLoadDataCommand

logger = logging.getLogger(__name__)


class Command(DjangoLoadDataCommand):
    """Load fixtures then ensure admin mailboxes exist."""

    def handle(self, *fixture_labels, **options):
        result = super().handle(*fixture_labels, **options)
        try:
            from apps.emails.models import ensure_admin_email_mailboxes

            ensure_admin_email_mailboxes()
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Unable to ensure admin email mailboxes after loaddata")
        return result
