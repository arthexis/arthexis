from django.contrib import admin
from django.test import RequestFactory
from django.utils import timezone

from apps.core.admin import EmailCollectorAdmin, EmailInboxAdmin
from apps.core.models import EmailTransaction
from apps.emails.models import EmailBridge, EmailCollector, EmailInbox, EmailOutbox
from apps.nodes.admin import EmailOutboxAdmin
from apps.users.models import User



def _create_owner(username: str) -> User:
    """Create a regular user for profile ownership in admin tests."""
    return User.objects.create_user(username=username, password="password")



def _create_inbox(owner: User, username: str) -> EmailInbox:
    """Create an inbox profile tied to ``owner``."""
    return EmailInbox.objects.create(
        user=owner,
        username=username,
        password="secret",
        host="imap.example.com",
        protocol=EmailInbox.IMAP,
    )



def _create_outbox(owner: User, username: str) -> EmailOutbox:
    """Create an outbox profile tied to ``owner``."""
    return EmailOutbox.objects.create(
        user=owner,
        host="smtp.example.com",
        port=587,
        username=username,
        password="secret",
        from_email=username,
    )



def test_email_inbox_admin_columns_and_annotations(db):
    """Inbox changelist should show username first plus collector and last-used metadata."""
    owner = _create_owner("inbox-owner")
    inbox = _create_inbox(owner, "inbox@example.com")
    EmailCollector.objects.create(inbox=inbox, name="enabled")
    EmailCollector.objects.create(inbox=inbox, name="disabled", is_enabled=False)
    EmailTransaction.objects.create(
        direction=EmailTransaction.INBOUND,
        inbox=inbox,
        processed_at=timezone.now(),
    )

    model_admin = EmailInboxAdmin(EmailInbox, admin.site)
    request = RequestFactory().get("/admin/emails/emailinbox/")

    row = model_admin.get_queryset(request).get(pk=inbox.pk)
    assert model_admin.list_display[:4] == (
        "username",
        "owner_label",
        "collector_count",
        "last_used_at",
    )
    assert model_admin.collector_count(row) == "1/2"
    assert model_admin.last_used_at(row) != "-"



def test_email_outbox_admin_columns_and_annotations(db):
    """Outbox changelist should mirror inbox metadata columns and computed values."""
    owner = _create_owner("outbox-owner")
    inbox = _create_inbox(owner, "relay-inbox@example.com")
    EmailCollector.objects.create(inbox=inbox, name="enabled")
    EmailCollector.objects.create(inbox=inbox, name="disabled", is_enabled=False)
    outbox = _create_outbox(owner, "outbox@example.com")
    EmailBridge.objects.create(inbox=inbox, outbox=outbox)
    EmailTransaction.objects.create(
        direction=EmailTransaction.OUTBOUND,
        outbox=outbox,
        processed_at=timezone.now(),
    )

    model_admin = EmailOutboxAdmin(EmailOutbox, admin.site)
    request = RequestFactory().get("/admin/emails/emailoutbox/")

    row = model_admin.get_queryset(request).get(pk=outbox.pk)
    assert model_admin.list_display[:4] == (
        "username",
        "owner_label",
        "collector_count",
        "last_used_at",
    )
    assert model_admin.collector_count(row) == "1/2"
    assert model_admin.last_used_at(row) != "-"


def test_email_collector_preview_template_renders(db):
    """Collector preview action should render without template lookup failures."""
    owner = _create_owner("collector-owner")
    inbox = _create_inbox(owner, "collector@example.com")
    collector = EmailCollector.objects.create(inbox=inbox, name="preview")

    model_admin = EmailCollectorAdmin(EmailCollector, admin.site)
    request = RequestFactory().get("/admin/emails/emailcollector/")
    response = model_admin.preview_messages(request, EmailCollector.objects.filter(pk=collector.pk))

    response.render()
    assert response.status_code == 200
