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
from awg.models import PowerLead


def test_show_invites_lists_recent_leads():
    InviteLead.objects.all().delete()
    PowerLead.objects.all().delete()

    now = timezone.now()
    old_invite = InviteLead.objects.create(email="old@example.com")
    old_invite.created_on = now - timedelta(days=1)
    old_invite.save(update_fields=["created_on"])
    power = PowerLead.objects.create(values={"meters": "1"})
    new_invite = InviteLead.objects.create(email="new@example.com")

    out = StringIO()
    call_command("show_invites", 2, stdout=out)
    output = out.getvalue()
    assert "new@example.com" in output
    assert "PowerLead" in output
    assert "old@example.com" not in output


def test_show_invites_filters_invite():
    InviteLead.objects.all().delete()
    PowerLead.objects.all().delete()

    InviteLead.objects.create(email="only@example.com")
    PowerLead.objects.create(values={"x": 1})

    out = StringIO()
    call_command("show_invites", "--invite", stdout=out)
    output = out.getvalue()
    assert "only@example.com" in output
    assert "PowerLead" not in output


def test_show_invites_filters_power():
    InviteLead.objects.all().delete()
    PowerLead.objects.all().delete()

    InviteLead.objects.create(email="only@example.com")
    PowerLead.objects.create(values={"x": 1})

    out = StringIO()
    call_command("show_invites", "--power", stdout=out)
    output = out.getvalue()
    assert "PowerLead" in output
    assert "only@example.com" not in output
