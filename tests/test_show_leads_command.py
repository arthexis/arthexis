import os
import sys
from pathlib import Path
from io import StringIO
from datetime import timedelta

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.core.management import call_command
from django.utils import timezone
from core.models import InviteLead
from teams.models import EmailOutbox
from awg.models import PowerLead


def test_show_leads_lists_recent_leads():
    InviteLead.objects.all().delete()
    PowerLead.objects.all().delete()

    now = timezone.now()
    old_invite = InviteLead.objects.create(email="old@example.com")
    old_invite.created_on = now - timedelta(days=1)
    old_invite.save(update_fields=["created_on"])
    power = PowerLead.objects.create(values={"meters": "1"})
    outbox = EmailOutbox.objects.create(
        host="smtp.example.com", username="noreply", port=587
    )
    new_invite = InviteLead.objects.create(email="new@example.com")
    new_invite.sent_on = now
    new_invite.sent_via_outbox = outbox
    new_invite.save(update_fields=["sent_on", "sent_via_outbox"])

    out = StringIO()
    call_command("show_leads", 2, stdout=out)
    output = out.getvalue()
    assert "InviteLead: new@example.com [SENT" in output
    assert f"via {outbox}" in output
    assert "PowerLead" in output
    assert "old@example.com" not in output


def test_show_leads_filters_invites():
    InviteLead.objects.all().delete()
    PowerLead.objects.all().delete()

    InviteLead.objects.create(email="only@example.com")
    PowerLead.objects.create(values={"x": 1})

    out = StringIO()
    call_command("show_leads", "--invites", stdout=out)
    output = out.getvalue()
    assert "only@example.com" in output
    assert "[NOT SENT]" in output
    assert "PowerLead" not in output


def test_show_leads_filters_power():
    InviteLead.objects.all().delete()
    PowerLead.objects.all().delete()

    InviteLead.objects.create(email="only@example.com")
    PowerLead.objects.create(values={"x": 1})

    out = StringIO()
    call_command("show_leads", "--power", stdout=out)
    output = out.getvalue()
    assert "PowerLead" in output
    assert "only@example.com" not in output
