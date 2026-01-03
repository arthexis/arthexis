import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.logbook.models import LogbookEntry
from apps.nodes.models import NetMessage, Node, NodeRole
from apps.users.models import User


@pytest.mark.django_db
def test_create_logbook_entry_with_attachments(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.LOG_DIR = tmp_path / "logs"
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = settings.LOG_DIR / "example.log"
    log_file.write_text("log content")

    NodeRole.objects.create(name="Watchtower")
    node = Node.objects.create(
        hostname="local",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
    )
    user = User.objects.create_user(username="tester", password="secret")
    client.force_login(user)

    image_content = SimpleUploadedFile("test.png", b"image-bytes", content_type="image/png")
    debug_payload = {"status": "ok"}

    response = client.post(
        reverse("logbook:create"),
        data={
            "title": "Test entry",
            "report": "Something happened",
            "event_at": "2024-01-01T00:00",
            "debug_info": json.dumps(debug_payload),
            "logs": ["example.log"],
            "images": [image_content],
        },
        follow=True,
    )

    assert response.status_code == 200
    if response.context and response.context[0].get("form"):
        assert not response.context[0]["form"].errors
    entry = LogbookEntry.objects.first()
    assert entry is not None
    assert entry.secret
    assert len(entry.secret) <= 16
    assert entry.debug_info == debug_payload
    assert entry.log_attachments.count() == 1
    assert entry.content_samples.count() == 1

    message = NetMessage.objects.filter(body=entry.secret).first()
    assert message is not None
    assert message.target_limit >= 1


@pytest.mark.django_db
def test_public_detail_view(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    entry = LogbookEntry.objects.create(title="Incident", report="Details")
    url = reverse("logbook:detail", args=[entry.secret])
    response = client.get(url)
    assert response.status_code == 200
    assert "Incident" in response.content.decode()
