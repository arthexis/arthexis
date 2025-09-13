import os
import sys
from pathlib import Path
from io import StringIO

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.urls import reverse
from core.models import InviteLead
from post_office.models import Email


def test_send_invite_generates_link_and_marks_sent():
    InviteLead.objects.all().delete()
    Email.objects.all().delete()
    User = get_user_model()
    user = User.objects.create_user(username="test", email="invite@example.com")
    InviteLead.objects.create(email="invite@example.com")

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    expected_login = reverse("pages:invitation-login", args=[uid, token])

    out = StringIO()
    call_command("send_invite", "invite@example.com", stdout=out)
    output = out.getvalue()
    expected_alt = expected_login.replace("invitation-login", "invitation")
    assert expected_login in output or expected_alt in output

    lead = InviteLead.objects.get(email="invite@example.com")
    assert lead.sent_on is not None
    assert Email.objects.count() == 1
