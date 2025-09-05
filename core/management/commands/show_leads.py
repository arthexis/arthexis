from django.core.management.base import BaseCommand
from core.models import InviteLead
from awg.models import PowerLead
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

        if show_invites:
            leads = list(InviteLead.objects.order_by("-created_on")[:limit])
        elif show_power:
            leads = list(PowerLead.objects.order_by("-created_on")[:limit])
        else:
            invites = list(InviteLead.objects.order_by("-created_on")[:limit])
            powers = list(PowerLead.objects.order_by("-created_on")[:limit])
            leads = sorted(invites + powers, key=lambda l: l.created_on, reverse=True)[
                :limit
            ]

        for lead in leads:
            if isinstance(lead, InviteLead):
                detail = lead.email
            else:
                detail = json.dumps(lead.values, sort_keys=True)
            self.stdout.write(
                f"{lead.created_on:%Y-%m-%d %H:%M:%S} {lead.__class__.__name__}: {detail}"
            )
