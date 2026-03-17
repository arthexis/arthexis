import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.awg.models import PowerLead
from apps.core.models import InviteLead


class Command(BaseCommand):
    """Display recent invite and/or power leads."""

    help = "Show the most recent invite or power leads"

    def add_arguments(self, parser):
        """Register CLI arguments.

        Args:
            parser: Argument parser used by Django management commands.
        """

        parser.add_argument(
            "n",
            nargs="?",
            type=int,
            default=5,
            help="Number of leads to display",
        )
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--invites",
            action="store_true",
            help="Show only InviteLead records",
        )
        group.add_argument(
            "--power",
            action="store_true",
            help="Show only PowerLead records",
        )

    def _normalize_timestamp(self, value):
        """Normalize a timestamp to localtime when timezone-aware.

        Args:
            value: Datetime value to normalize.

        Returns:
            Datetime in local timezone when aware, unchanged when naive.
        """

        if timezone.is_naive(value):  # pragma: no cover - depends on USE_TZ
            return value
        return timezone.localtime(value)

    def get_leads(self, limit: int, show_invites: bool, show_power: bool):
        """Return lead records based on filtering flags and desired result size."""

        invite_leads = InviteLead.objects.select_related("sent_via_outbox")
        if show_invites:
            return list(invite_leads.order_by("-created_on")[:limit])
        if show_power:
            return list(PowerLead.objects.order_by("-created_on")[:limit])

        invites = list(invite_leads.order_by("-created_on")[:limit])
        powers = list(PowerLead.objects.order_by("-created_on")[:limit])
        return sorted(invites + powers, key=lambda lead: lead.created_on, reverse=True)[:limit]

    def format_lead(self, lead) -> str:
        """Render a lead row as printable text."""

        created_on = self._normalize_timestamp(lead.created_on)
        if isinstance(lead, InviteLead):
            detail = lead.email
            if lead.sent_on:
                sent_on = self._normalize_timestamp(lead.sent_on)
                status = f" [SENT {sent_on:%Y-%m-%d %H:%M:%S}]"
                if lead.sent_via_outbox_id:
                    status += f" via {lead.sent_via_outbox}"
            else:
                status = " [NOT SENT]"
        else:
            detail = json.dumps(lead.values, sort_keys=True)
            status = ""

        return f"{created_on:%Y-%m-%d %H:%M:%S} {lead.__class__.__name__}: {detail}{status}"

    def handle(self, *args, **options):
        """Print formatted lead rows for the selected lead source and limit.

        Raises:
            CommandError: If n is not a positive integer.
        """

        limit = options.get("n")
        if not isinstance(limit, int) or limit <= 0:
            raise CommandError("n must be a positive integer")

        leads = self.get_leads(limit, options["invites"], options["power"])
        for lead in leads:
            self.stdout.write(self.format_lead(lead))
