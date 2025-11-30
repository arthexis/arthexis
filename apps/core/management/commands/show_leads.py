from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.awg.models import PowerLead
from apps.core.models import InviteLead
import json


class Command(BaseCommand):
    """Display recent invite or power leads."""

    help = "Show the most recent invite or power leads"

    def add_arguments(self, parser):
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

    def handle(self, *args, **options):
        limit = options["n"]
        show_invites = options["invites"]
        show_power = options["power"]

        invite_leads = InviteLead.objects.select_related("sent_via_outbox")
        if show_invites:
            leads = list(invite_leads.order_by("-created_on")[:limit])
        elif show_power:
            leads = list(PowerLead.objects.order_by("-created_on")[:limit])
        else:
            invites = list(invite_leads.order_by("-created_on")[:limit])
            powers = list(PowerLead.objects.order_by("-created_on")[:limit])
            leads = sorted(invites + powers, key=lambda l: l.created_on, reverse=True)[
                :limit
            ]

        def _normalize_timestamp(value):
            if timezone.is_naive(value):  # pragma: no cover - depends on USE_TZ
                return value
            return timezone.localtime(value)

        for lead in leads:
            created_on = _normalize_timestamp(lead.created_on)
            if isinstance(lead, InviteLead):
                detail = lead.email
                if lead.sent_on:
                    sent_on = _normalize_timestamp(lead.sent_on)
                    status = f" [SENT {sent_on:%Y-%m-%d %H:%M:%S}]"
                    if lead.sent_via_outbox_id:
                        status += f" via {lead.sent_via_outbox}"
                else:
                    status = " [NOT SENT]"
            else:
                detail = json.dumps(lead.values, sort_keys=True)
                status = ""
            self.stdout.write(
                f"{created_on:%Y-%m-%d %H:%M:%S} {lead.__class__.__name__}: {detail}{status}"
            )
