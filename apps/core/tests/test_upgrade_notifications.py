from __future__ import annotations

import json
from pathlib import Path

import pytest

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings

from apps.core.models import EmailTransaction
from apps.core.services.upgrade_notifications import notify_upgrade_completion
from apps.users import temp_passwords


@pytest.mark.django_db
@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_ADMIN_EMAIL="tecnologia@gelectriic.com",
    DEFAULT_ADMIN_USERNAME="arthexis",
    DEFAULT_FROM_EMAIL="tecnologia@gelectriic.com",
)
def test_notify_upgrade_completion_sends_email_and_rotates_temp_password(
    monkeypatch, settings, tmp_path: Path
) -> None:
    settings.TEMP_PASSWORD_LOCK_DIR = str(tmp_path / ".locks" / "temp-passwords")
    (tmp_path / ".locks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "VERSION").write_text("0.2.3\n", encoding="utf-8")
    (tmp_path / ".locks" / "upgrade_duration.lck").write_text(
        json.dumps(
            {
                "started_at": "2026-04-23T01:00:00+00:00",
                "finished_at": "2026-04-23T01:02:00+00:00",
                "duration_seconds": 120,
                "status": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "apps.core.services.upgrade_notifications._resolve_local_node",
        lambda: None,
    )
    monkeypatch.setattr(
        "apps.core.services.upgrade_notifications._read_current_revision",
        lambda _base_dir: "abcdef1234567890",
    )

    result = notify_upgrade_completion(
        base_dir=tmp_path,
        exit_status=0,
        source="upgrade.sh",
        channel="stable",
        branch="main",
        service_name="arthexis-main",
        initial_version="0.2.2",
        target_version="0.2.3",
        initial_revision="111111111111",
        target_revision="222222222222",
    )

    assert result.email_sent is True
    assert result.admin_username == "arthexis"
    assert result.recipients == ["tecnologia@gelectriic.com"]
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["tecnologia@gelectriic.com"]
    assert "Upgrade succeeded" in message.subject
    assert "Temporary password:" in message.body
    assert result.temp_password in message.body
    assert "Initial version: 0.2.2" in message.body
    assert "Current version: 0.2.3" in message.body
    assert "Duration seconds: 120" in message.body

    user = get_user_model().all_objects.get(username="arthexis")
    entry = temp_passwords.load_temp_password(user.username)
    assert entry is not None
    assert entry.check_password(result.temp_password)
    assert EmailTransaction.objects.count() == 0


@pytest.mark.django_db
@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_ADMIN_EMAIL="tecnologia@gelectriic.com",
    DEFAULT_ADMIN_USERNAME="ops-admin",
    DEFAULT_FROM_EMAIL="tecnologia@gelectriic.com",
)
def test_notify_upgrade_completion_repairs_existing_default_admin_user(
    monkeypatch, settings, tmp_path: Path
) -> None:
    settings.TEMP_PASSWORD_LOCK_DIR = str(tmp_path / ".locks" / "temp-passwords")
    user_model = get_user_model()
    user = user_model.all_objects.create_user(
        username="ops-admin",
        email="wrong@example.com",
        is_active=False,
        is_staff=False,
        is_superuser=False,
        allow_local_network_passwordless_login=True,
    )
    user.temporary_expires_at = None
    user.operate_as = user_model.objects.create(username="delegate")
    user.is_deleted = True
    user.save()

    monkeypatch.setattr(
        "apps.core.services.upgrade_notifications._resolve_local_node",
        lambda: None,
    )
    monkeypatch.setattr(
        "apps.core.services.upgrade_notifications._read_current_revision",
        lambda _base_dir: "fedcba9876543210",
    )

    result = notify_upgrade_completion(base_dir=tmp_path, exit_status=1)

    user.refresh_from_db()
    assert result.email_sent is True
    assert user.email == "tecnologia@gelectriic.com"
    assert user.is_active is True
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.is_deleted is False
    assert user.allow_local_network_passwordless_login is False
    assert user.operate_as_id is None
