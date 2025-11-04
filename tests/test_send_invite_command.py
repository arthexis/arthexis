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
from nodes.models import Node, EmailOutbox
from django.core import mail
from django.test import override_settings


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_send_invite_generates_link_and_marks_sent():
    InviteLead.objects.all().delete()
    if hasattr(mail, "outbox"):
        mail.outbox.clear()
    else:
        mail.outbox = []
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
    assert lead.sent_via_outbox_id is None
    assert len(mail.outbox) == 1


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_send_invite_tracks_outbox(monkeypatch):
    InviteLead.objects.all().delete()
    User = get_user_model()
    email = "outbox@example.com"
    User.objects.create_user(username="outbox", email=email)
    lead = InviteLead.objects.create(email=email)
    node = Node.objects.create(
        hostname="node-host",
        address="127.0.0.1",
        port=8888,
        mac_address="00:aa:bb:cc:dd:ee",
    )
    outbox = EmailOutbox.objects.create(
        node=node,
        host="smtp.example.com",
        username="mailer",
        port=587,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))
    monkeypatch.setattr("nodes.models.mailer.send", lambda *args, **kwargs: "ok")

    out = StringIO()
    call_command("send_invite", email, stdout=out)

    lead.refresh_from_db()
    assert lead.sent_via_outbox == outbox
