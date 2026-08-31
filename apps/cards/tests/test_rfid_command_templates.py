from __future__ import annotations

import json
import time
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from apps.cards import command_layout
from apps.cards.card_commands import (
    MAX_COMMAND_CAPTURE_CHARS,
    execute_command_card_payload,
)
from apps.cards.command_burn import CommandCardBurnError
from apps.cards.models import (
    RFID,
    RFIDAttempt,
    RFIDCommandExecution,
    RFIDCommandTemplate,
)
from apps.cards.rfid_names import generated_label_for_rfid

pytestmark = [pytest.mark.django_db]


def _dump_from_blocks(blocks):
    return [{"block": block, "data": data} for block, data in blocks.items()]


def _writer(**kwargs):
    result_payload = kwargs["result_payload"]
    digest = command_layout.result_digest(result_payload)
    return {"result": result_payload, "result_digest": digest}


def _create_health_snapshot_template():
    return RFIDCommandTemplate.objects.create(
        name="HEALTH SNAPSHOT",
        slug="health-snapshot",
        command_name="SUITE_COMMAND",
        command_params={"command": "health", "args": ["--all"]},
        source=RFIDCommandTemplate.Source.BUNDLED,
    )


def test_command_template_rejects_non_object_command_json():
    template = RFIDCommandTemplate(
        name="BAD JSON",
        slug="bad-json",
        command_name="LOG",
        command_params=[],
        command_sigils=[],
    )

    with pytest.raises(ValidationError) as exc_info:
        template.save()

    assert "command_params" in exc_info.value.message_dict
    assert "command_sigils" in exc_info.value.message_dict


def test_command_card_lists_available_templates():
    _create_health_snapshot_template()
    stdout = StringIO()

    call_command("rfid", "command-card", "--list-commands", stdout=stdout)

    output = stdout.getvalue()
    assert "Available RFID command templates" in output
    assert "HEALTH SNAPSHOT" in output
    assert "/cards/command-templates/health-snapshot/" in output


def test_command_card_write_command_burns_template(monkeypatch):
    template = RFIDCommandTemplate.objects.create(
        name="TEST COMMAND",
        slug="test-command",
        command_name="SUITE_COMMAND",
        command_params={"command": "uptime", "args": [], "timeout": 60},
        lifecycle_mode="reader_held",
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    tag = RFID.objects.create(rfid="ABCD1234")
    captured: dict[str, object] = {}

    def fake_write_current_card_command(**kwargs):
        captured.update(kwargs)
        return {"rfid": tag.rfid, "label_id": tag.pk}

    monkeypatch.setattr(
        "apps.cards.management.commands.rfid.write_current_card_command",
        fake_write_current_card_command,
    )
    stdout = StringIO()

    call_command(
        "rfid",
        "command-card",
        "--write-command",
        "test-command",
        "--pretty",
        stdout=stdout,
    )

    tag.refresh_from_db()
    result = json.loads(stdout.getvalue())
    assert captured["name"] == template.name
    assert captured["command"] == "SUITE_COMMAND"
    assert captured["params"]["command"] == "uptime"
    assert captured["lifecycle_mode"] == "reader_held"
    assert tag.command_template == template
    assert result["template"] == template.name
    assert result["template_url"] == template.get_absolute_url()


def test_command_card_write_command_prints_tracking_label(
    monkeypatch, settings, tmp_path
):
    settings.PUBLIC_BASE_URL = "https://suite.example"
    template = RFIDCommandTemplate.objects.create(
        name="TEST COMMAND",
        slug="test-command",
        command_name="SUITE_COMMAND",
        command_params={"command": "uptime", "args": [], "timeout": 60},
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    tag = RFID.objects.create(rfid="ABCD1234")

    def fake_write_current_card_command(**kwargs):
        return {"rfid": tag.rfid, "label_id": tag.pk}

    monkeypatch.setattr(
        "apps.cards.management.commands.rfid.write_current_card_command",
        fake_write_current_card_command,
    )
    preview_path = tmp_path / "command-card-label.png"
    stdout = StringIO()

    call_command(
        "rfid",
        "command-card",
        "--write-command",
        "test-command",
        "--print-label",
        "--label-dry-run",
        "--label-output",
        str(preview_path),
        "--pretty",
        stdout=stdout,
    )

    tag.refresh_from_db()
    result = json.loads(stdout.getvalue())
    assert tag.command_template == template
    assert preview_path.is_file()
    assert result["label_print"]["payload"] == (
        "https://suite.example/cards/command-templates/test-command/"
    )
    assert result["label_print"]["template"] == template.name
    assert result["label_print"]["card_label"] == tag.generated_label
    assert result["label_print"]["command_bytes"] > 0
    assert result["label_print"]["dry_run"] is True
    assert result["label_print"]["printed"] is False


def test_command_card_burn_uses_previous_scanned_template(monkeypatch):
    template = RFIDCommandTemplate.objects.create(
        name="COPY SOURCE",
        slug="copy-source",
        command_name="LOG",
        command_params={"message": "copy"},
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    source_tag = RFID.objects.create(
        rfid="ABCD1234",
        command_template=template,
        command_card_name=template.name,
    )
    RFIDAttempt.objects.create(
        rfid=source_tag.rfid,
        label=source_tag,
        status=RFIDAttempt.Status.SCANNED,
        source=RFIDAttempt.Source.SERVICE,
        payload={"rfid": source_tag.rfid, "label_id": source_tag.pk},
    )
    target_tag = RFID.objects.create(rfid="F00DCAFE")
    captured: dict[str, object] = {}

    def fake_write_current_card_command(**kwargs):
        captured.update(kwargs)
        return {"rfid": target_tag.rfid, "label_id": target_tag.pk}

    monkeypatch.setattr(
        "apps.cards.management.commands.rfid.write_current_card_command",
        fake_write_current_card_command,
    )
    stdout = StringIO()

    call_command("rfid", "command-card", "burn", "--pretty", stdout=stdout)

    target_tag.refresh_from_db()
    result = json.loads(stdout.getvalue())
    assert captured["name"] == template.name
    assert captured["command"] == template.command_name
    assert captured["params"] == template.command_params
    assert captured["timeout"] == 30.0
    assert target_tag.command_template == template
    assert result["template_source"] == "previous_scan"
    assert result["source_label_id"] == source_tag.pk


def test_command_card_print_label_uses_template_qr_target_path(
    monkeypatch, settings, tmp_path
):
    settings.PUBLIC_BASE_URL = "https://suite.example"
    template = RFIDCommandTemplate.objects.create(
        name="BURN GWAY IMAGE",
        slug="burn-gway-image",
        title="Burn GWAY Image",
        command_name="SUITE_COMMAND",
        command_params={"command": "imager", "args": ["gway-burn"], "timeout": 3600},
        source=RFIDCommandTemplate.Source.CUSTOM,
        qr_target_path="/imager/burn/",
    )
    tag = RFID.objects.create(rfid="ABCD1234")

    def fake_write_current_card_command(**kwargs):
        return {"rfid": tag.rfid, "label_id": tag.pk}

    monkeypatch.setattr(
        "apps.cards.management.commands.rfid.write_current_card_command",
        fake_write_current_card_command,
    )
    preview_path = tmp_path / "gway-burn-label.png"
    stdout = StringIO()

    call_command(
        "rfid",
        "command-card",
        "--write-command",
        template.slug,
        "--print-label",
        "--label-printer",
        "none",
        "--label-output",
        str(preview_path),
        "--pretty",
        stdout=stdout,
    )

    result = json.loads(stdout.getvalue())
    assert result["label_print"]["payload"] == (
        "https://suite.example/imager/burn/"
    )


def test_command_card_label_existing_card_does_not_rewrite(
    monkeypatch, settings, tmp_path
):
    settings.PUBLIC_BASE_URL = "https://suite.example"
    template = RFIDCommandTemplate.objects.create(
        name="TRACK CARD",
        slug="track-card",
        command_name="LOG",
        command_params={"message": "track"},
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    tag = RFID.objects.create(
        rfid="BEEF1234",
        command_card_name=template.name,
        command_template=template,
    )
    generated_label = generated_label_for_rfid(tag.rfid)

    def fail_write_current_card_command(**kwargs):
        pytest.fail("label-only printing must not rewrite the RFID card")

    monkeypatch.setattr(
        "apps.cards.management.commands.rfid.write_current_card_command",
        fail_write_current_card_command,
    )
    preview_path = tmp_path / "existing-card-label.png"
    stdout = StringIO()

    call_command(
        "rfid",
        "command-card",
        "label",
        "--card",
        generated_label,
        "--label-printer",
        "none",
        "--label-output",
        str(preview_path),
        "--pretty",
        stdout=stdout,
    )

    tag.refresh_from_db()
    result = json.loads(stdout.getvalue())
    assert preview_path.is_file()
    assert tag.command_template == template
    assert tag.generated_label == generated_label
    assert result["template"] == template.name
    assert result["card_label"] == generated_label
    assert result["label_print"]["payload"] == (
        "https://suite.example/cards/command-templates/track-card/"
    )
    assert result["label_print"]["printer"] == "none"
    assert result["label_print"]["printed"] is False


def test_command_card_write_print_label_creates_tracking_template(
    monkeypatch, settings, tmp_path
):
    settings.PUBLIC_BASE_URL = "https://suite.example"
    tag = RFID.objects.create(rfid="CAFE1234")
    captured: dict[str, object] = {}

    def fake_write_current_card_command(**kwargs):
        captured.update(kwargs)
        return {"rfid": tag.rfid, "label_id": tag.pk}

    monkeypatch.setattr(
        "apps.cards.management.commands.rfid.write_current_card_command",
        fake_write_current_card_command,
    )
    preview_path = tmp_path / "ad-hoc-command-card-label.png"
    stdout = StringIO()

    call_command(
        "rfid",
        "command-card",
        "write",
        "--name",
        "AD HOC",
        "--command",
        "LOG",
        "--params-json",
        '{"message": "hello"}',
        "--lifecycle-mode",
        "reader_held",
        "--print-label",
        "--label-printer",
        "none",
        "--label-output",
        str(preview_path),
        "--pretty",
        stdout=stdout,
    )

    tag.refresh_from_db()
    template = RFIDCommandTemplate.objects.get(name="AD HOC")
    result = json.loads(stdout.getvalue())
    assert preview_path.is_file()
    assert tag.command_template == template
    assert template.command_name == "LOG"
    assert template.command_params == {"message": "hello"}
    assert template.lifecycle_mode == "reader_held"
    assert captured["lifecycle_mode"] == "reader_held"
    assert result["template"] == "AD HOC"
    assert result["label_print"]["payload"] == (
        "https://suite.example/cards/command-templates/ad-hoc/"
    )
    assert result["label_print"]["printer"] == "none"


def test_command_card_write_print_label_updates_existing_template_lifecycle(
    monkeypatch, settings, tmp_path
):
    settings.PUBLIC_BASE_URL = "https://suite.example"
    template = RFIDCommandTemplate.objects.create(
        name="AD HOC",
        slug="ad-hoc",
        command_name="LOG",
        command_params={"message": "hello"},
        lifecycle_mode="triggered",
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    tag = RFID.objects.create(rfid="CAFE1234")

    def fake_write_current_card_command(**kwargs):
        return {"rfid": tag.rfid, "label_id": tag.pk}

    monkeypatch.setattr(
        "apps.cards.management.commands.rfid.write_current_card_command",
        fake_write_current_card_command,
    )
    preview_path = tmp_path / "ad-hoc-command-card-label.png"

    call_command(
        "rfid",
        "command-card",
        "write",
        "--name",
        "AD HOC",
        "--command",
        "LOG",
        "--params-json",
        '{"message": "hello"}',
        "--lifecycle-mode",
        "reader_held",
        "--print-label",
        "--label-printer",
        "none",
        "--label-output",
        str(preview_path),
        stdout=StringIO(),
    )

    template.refresh_from_db()
    tag.refresh_from_db()
    assert template.lifecycle_mode == "reader_held"
    assert tag.command_template == template


def test_discovered_command_card_creates_inactive_template():
    blocks, metadata = command_layout.build_command_card_blocks(
        name="WILD COMMAND",
        command="LOG",
        params={"message": "hello"},
        provenance_key="AABBCCDDEEFF0011",
    )

    execution = execute_command_card_payload(
        {"rfid": "ABCD1234", "dump": _dump_from_blocks(blocks)},
        reader_id="reader-1",
    )

    template = RFIDCommandTemplate.objects.get(name="WILD COMMAND")
    assert template.source == RFIDCommandTemplate.Source.DISCOVERED
    assert template.is_active is False
    assert template.command_name == "LOG"
    assert execution.template == template
    assert execution.status == RFIDCommandExecution.Status.BLOCKED
    assert metadata.command_block_count >= 1


def test_public_template_view_hides_run_output_from_anonymous_users(client):
    user = get_user_model().objects.create_user(username="template-owner")
    template = RFIDCommandTemplate.objects.create(
        name="VIEW TEST",
        slug="view-test",
        title="View Test",
        description="Public command view.",
        instructions="Hold until complete.",
        command_name="LOG",
        command_params={"message": "hi"},
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    RFIDCommandExecution.objects.create(
        template=template,
        rfid_value="ABCD1234",
        card_name=template.name,
        reader_id="reader-1",
        command_name=template.command_name,
        command_params=template.command_params,
        run_as_user=user,
        status=RFIDCommandExecution.Status.SUCCEEDED,
        result={"summary": "view test finished", "payload": {"stdout": "ok"}},
    )

    response = client.get(template.get_absolute_url())

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "View Test" in content
    assert "Hold until complete." in content
    assert "view test finished" not in content
    assert "reader-1" not in content
    assert "Past Runs" not in content
    assert "Card Consistency" not in content


def test_public_template_view_uses_qr_target_path(client):
    template = RFIDCommandTemplate.objects.create(
        name="BURN GWAY IMAGE",
        slug="burn-gway-image",
        title="Burn GWAY Image",
        command_name="SUITE_COMMAND",
        command_params={"command": "imager", "args": ["gway-burn"], "timeout": 3600},
        source=RFIDCommandTemplate.Source.CUSTOM,
        qr_target_path="/imager/burn/",
    )

    response = client.get(template.get_absolute_url())

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "http://testserver/imager/burn/" in content


def test_command_card_burn_picker_lists_available_templates(client):
    user = get_user_model().objects.create_user(
        username="burner",
        is_staff=True,
    )
    RFIDCommandTemplate.objects.create(
        name="ACTIVE BURN",
        slug="active-burn",
        title="Active Burn",
        command_name="LOG",
        command_params={"message": "active"},
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    RFIDCommandTemplate.objects.create(
        name="INACTIVE BURN",
        slug="inactive-burn",
        title="Inactive Burn",
        command_name="LOG",
        command_params={"message": "inactive"},
        source=RFIDCommandTemplate.Source.CUSTOM,
        is_active=False,
    )
    client.force_login(user)

    response = client.get(reverse("rfid-command-template-burn"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Active Burn" in content
    assert "Inactive Burn" not in content
    assert "Copy Previous Card" in content


def test_command_card_burn_picker_writes_selected_template(monkeypatch, client):
    user = get_user_model().objects.create_user(
        username="burner",
        is_staff=True,
    )
    template = RFIDCommandTemplate.objects.create(
        name="PICK BURN",
        slug="pick-burn",
        command_name="LOG",
        command_params={"message": "picked"},
        lifecycle_mode="reader_held",
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    target_tag = RFID.objects.create(rfid="FACEFEED")
    captured: dict[str, object] = {}

    def fake_write_current_card_command(**kwargs):
        captured.update(kwargs)
        return {"rfid": target_tag.rfid, "label_id": target_tag.pk}

    monkeypatch.setattr(
        "apps.cards.views.write_current_card_command",
        fake_write_current_card_command,
    )
    client.force_login(user)

    response = client.post(
        reverse("rfid-command-template-burn"),
        {"template": template.slug},
    )

    target_tag.refresh_from_db()
    assert response.status_code == 200
    assert captured["name"] == template.name
    assert captured["command"] == template.command_name
    assert captured["params"] == template.command_params
    assert captured["lifecycle_mode"] == "reader_held"
    assert captured["timeout"] == 30.0
    assert target_tag.command_template == template
    assert target_tag.owner_user == user
    assert "PICK BURN" in response.content.decode("utf-8")


def test_command_card_burn_picker_sanitizes_json_write_errors(monkeypatch, client):
    user = get_user_model().objects.create_user(
        username="burner",
        is_staff=True,
    )
    template = RFIDCommandTemplate.objects.create(
        name="ERR BURN",
        slug="err-burn",
        command_name="LOG",
        command_params={"message": "error"},
        source=RFIDCommandTemplate.Source.CUSTOM,
    )

    def fake_write_current_card_command(**kwargs):
        return {
            "error": "RuntimeError: stack trace /secret/path",
            "exception": "traceback",
        }

    monkeypatch.setattr(
        "apps.cards.views.write_current_card_command",
        fake_write_current_card_command,
    )
    client.force_login(user)

    response = client.post(
        reverse("rfid-command-template-burn"),
        {"template": template.slug},
        HTTP_ACCEPT="application/json",
    )

    payload = response.json()
    assert response.status_code == 400
    assert payload["error"] == "RFID operation failed"
    assert payload["result"]["error"] == "RFID operation failed"
    assert "RuntimeError" not in response.content.decode("utf-8")
    assert "/secret/path" not in response.content.decode("utf-8")


def test_command_card_burn_picker_sanitizes_json_source_errors(monkeypatch, client):
    user = get_user_model().objects.create_user(
        username="burner",
        is_staff=True,
    )

    def fail_resolve_command_card_burn_source(_selected_value):
        raise CommandCardBurnError("Traceback (most recent call last): /secret/path")

    monkeypatch.setattr(
        "apps.cards.views.resolve_command_card_burn_source",
        fail_resolve_command_card_burn_source,
    )
    client.force_login(user)

    response = client.post(
        reverse("rfid-command-template-burn"),
        {"template": "missing-template"},
        HTTP_ACCEPT="application/json",
    )

    payload = response.json()
    content = response.content.decode("utf-8")
    assert response.status_code == 400
    assert payload["error"] == "Command template unavailable"
    assert payload["result"] is None
    assert "Traceback" not in content
    assert "/secret/path" not in content


def test_command_card_burn_picker_uses_previous_scanned_template(
    monkeypatch,
    client,
):
    user = get_user_model().objects.create_user(
        username="burner",
        is_staff=True,
    )
    template = RFIDCommandTemplate.objects.create(
        name="PREV BURN",
        slug="prev-burn",
        command_name="LOG",
        command_params={"message": "previous"},
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    source_tag = RFID.objects.create(
        rfid="ABCD1234",
        command_template=template,
        command_card_name=template.name,
    )
    RFIDAttempt.objects.create(
        rfid=source_tag.rfid,
        label=source_tag,
        status=RFIDAttempt.Status.SCANNED,
        source=RFIDAttempt.Source.SERVICE,
        payload={"rfid": source_tag.rfid, "label_id": source_tag.pk},
    )
    target_tag = RFID.objects.create(rfid="DEADBEEF")
    captured: dict[str, object] = {}

    def fake_write_current_card_command(**kwargs):
        captured.update(kwargs)
        return {"rfid": target_tag.rfid, "label_id": target_tag.pk}

    monkeypatch.setattr(
        "apps.cards.views.write_current_card_command",
        fake_write_current_card_command,
    )
    client.force_login(user)

    response = client.post(reverse("rfid-command-template-burn"), {})

    target_tag.refresh_from_db()
    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert captured["name"] == template.name
    assert target_tag.command_template == template
    assert source_tag.display_label in content
    assert "PREV BURN" in content


def test_public_template_view_shows_last_run_and_card_consistency_to_staff(client):
    user = get_user_model().objects.create_user(
        username="template-owner",
        is_staff=True,
    )
    template = RFIDCommandTemplate.objects.create(
        name="VIEW TEST",
        slug="view-test",
        title="View Test",
        description="Public command view.",
        instructions="Hold until complete.",
        command_name="LOG",
        command_params={"message": "hi"},
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    result_payload = {"summary": "view test finished", "payload": {"stdout": "ok"}}
    tag = RFID.objects.create(
        rfid="ABCD1234",
        command_template=template,
        command_card_name=template.name,
        command_payload_digest=template.payload_digest,
    )
    RFIDCommandExecution.objects.create(
        rfid=tag,
        template=template,
        rfid_value=tag.rfid,
        card_name=template.name,
        reader_id="reader-1",
        command_name=template.command_name,
        command_params=template.command_params,
        run_as_user=user,
        status=RFIDCommandExecution.Status.SUCCEEDED,
        result=result_payload,
    )
    client.force_login(user)

    response = client.get(template.get_absolute_url())

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "View Test" in content
    assert "Hold until complete." in content
    assert "view test finished" in content
    assert "reader-1" in content
    assert "valid" in content


def test_template_card_consistency_normalizes_result_digest_case():
    template = RFIDCommandTemplate.objects.create(
        name="CASE TEST",
        slug="case-test",
        command_name="LOG",
        command_params={"message": "hi"},
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    tag = RFID.objects.create(
        rfid="ABCD1234",
        command_template=template,
        command_card_name=template.name,
        command_payload_digest=template.payload_digest,
        command_result_digest="abcdef",
    )
    execution = RFIDCommandExecution.objects.create(
        rfid=tag,
        template=template,
        rfid_value=tag.rfid,
        card_name=template.name,
        command_name=template.command_name,
        result_digest="ABCDEF",
        status=RFIDCommandExecution.Status.SUCCEEDED,
    )

    check = template.card_consistency(tag, latest_execution=execution)

    assert check["result_matches"] is True
    assert check["valid"] is True


def test_template_card_consistency_detects_lifecycle_mismatch():
    template = RFIDCommandTemplate.objects.create(
        name="HELD TEST",
        slug="held-test",
        command_name="LOG",
        command_params={"message": "hi"},
        lifecycle_mode="reader_held",
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    tag = RFID.objects.create(
        rfid="ABCD1234",
        command_template=template,
        command_card_name=template.name,
        command_card_metadata={"lifecycle_mode": "triggered"},
        command_payload_digest=template.payload_digest,
    )

    check = template.card_consistency(tag)

    assert check["payload_matches"] is True
    assert check["lifecycle_matches"] is False
    assert check["card_lifecycle_mode"] == "triggered"
    assert check["valid"] is False


def test_public_template_view_preserves_falsy_latest_result_to_staff(client):
    user = get_user_model().objects.create_user(
        username="template-owner",
        is_staff=True,
    )
    template = RFIDCommandTemplate.objects.create(
        name="FALSY RESULT",
        slug="falsy-result",
        title="Falsy Result",
        command_name="LOG",
        command_params={"message": "hi"},
        source=RFIDCommandTemplate.Source.CUSTOM,
    )
    RFIDCommandExecution.objects.create(
        template=template,
        rfid_value="ABCD1234",
        card_name=template.name,
        command_name=template.command_name,
        result=[],
        status=RFIDCommandExecution.Status.SUCCEEDED,
    )
    client.force_login(user)

    response = client.get(template.get_absolute_url())

    assert response.status_code == 200
    assert "[]" in response.content.decode("utf-8")


def test_suite_command_card_requires_privileged_owner_permission():
    user = get_user_model().objects.create_user(username="template-owner")
    blocks, metadata = command_layout.build_command_card_blocks(
        name="HEALTH SNAPSHOT",
        command="SUITE_COMMAND",
        params={"command": "health", "args": []},
        provenance_key="AABBCCDDEEFF0011",
    )
    tag = RFID.objects.create(
        rfid="ABCD1234",
        command_card_name="HEALTH SNAPSHOT",
        command_payload_digest=command_layout.command_payload_digest(
            name="HEALTH SNAPSHOT",
            command="SUITE_COMMAND",
            params={"command": "health", "args": []},
        ),
        command_provenance_key=metadata.provenance_key,
        owner_user=user,
    )

    execution = execute_command_card_payload(
        {"rfid": tag.rfid, "label_id": tag.pk, "dump": _dump_from_blocks(blocks)},
        reader_id="reader-1",
        result_writer=lambda **kwargs: pytest.fail("blocked cards are not written"),
    )

    assert execution.status == RFIDCommandExecution.Status.BLOCKED
    assert execution.status_detail == "missing permission: cards.run_suite_command_card"


def test_suite_command_execution_errors_are_recorded(monkeypatch):
    user = get_user_model().objects.create_user(username="template-runner")
    user.user_permissions.add(Permission.objects.get(codename="run_suite_command_card"))
    blocks, metadata = command_layout.build_command_card_blocks(
        name="HEALTH SNAPSHOT",
        command="SUITE_COMMAND",
        params={"command": "health", "args": []},
        provenance_key="AABBCCDDEEFF0011",
    )
    tag = RFID.objects.create(
        rfid="ABCD1234",
        command_card_name="HEALTH SNAPSHOT",
        command_payload_digest=command_layout.command_payload_digest(
            name="HEALTH SNAPSHOT",
            command="SUITE_COMMAND",
            params={"command": "health", "args": []},
        ),
        command_provenance_key=metadata.provenance_key,
        owner_user=user,
    )

    def failing_suite_command(*_args, **_kwargs):
        raise OSError("missing python")

    monkeypatch.setattr(
        "apps.cards.card_commands._call_suite_management_command",
        failing_suite_command,
    )

    execution = execute_command_card_payload(
        {"rfid": tag.rfid, "label_id": tag.pk, "dump": _dump_from_blocks(blocks)},
        reader_id="reader-1",
        result_writer=_writer,
    )

    assert execution.status == RFIDCommandExecution.Status.FAILED
    assert execution.result["summary"] == "Failed to execute health: missing python"
    assert execution.result["payload"]["error"] == "missing python"


def test_suite_command_bounds_output_before_queueing(monkeypatch):
    user = get_user_model().objects.create_user(username="template-runner")
    user.user_permissions.add(Permission.objects.get(codename="run_suite_command_card"))
    blocks, metadata = command_layout.build_command_card_blocks(
        name="HEALTH SNAPSHOT",
        command="SUITE_COMMAND",
        params={"command": "health", "args": [], "timeout": 30},
        provenance_key="AABBCCDDEEFF0011",
    )
    tag = RFID.objects.create(
        rfid="ABCD1234",
        command_card_name="HEALTH SNAPSHOT",
        command_payload_digest=command_layout.command_payload_digest(
            name="HEALTH SNAPSHOT",
            command="SUITE_COMMAND",
            params={"command": "health", "args": [], "timeout": 30},
        ),
        command_provenance_key=metadata.provenance_key,
        owner_user=user,
    )
    stdout_text = "x" * (MAX_COMMAND_CAPTURE_CHARS + 128)
    stderr_text = "y" * (MAX_COMMAND_CAPTURE_CHARS + 128)

    monkeypatch.setattr(
        "apps.cards.card_commands._call_suite_management_command",
        lambda *_args, **_kwargs: (0, stdout_text, stderr_text),
    )

    execution = execute_command_card_payload(
        {"rfid": tag.rfid, "label_id": tag.pk, "dump": _dump_from_blocks(blocks)},
        reader_id="reader-1",
        result_writer=_writer,
    )

    assert execution.status == RFIDCommandExecution.Status.SUCCEEDED
    assert (
        execution.result["payload"]["stdout"]
        == stdout_text[-MAX_COMMAND_CAPTURE_CHARS:]
    )
    assert (
        execution.result["payload"]["stderr"]
        == stderr_text[-MAX_COMMAND_CAPTURE_CHARS:]
    )


def test_suite_command_honors_template_timeout(monkeypatch):
    user = get_user_model().objects.create_user(username="template-runner")
    user.user_permissions.add(Permission.objects.get(codename="run_suite_command_card"))
    blocks, metadata = command_layout.build_command_card_blocks(
        name="HEALTH SNAPSHOT",
        command="SUITE_COMMAND",
        params={"command": "health", "args": [], "timeout": 0.01},
        provenance_key="AABBCCDDEEFF0011",
    )
    tag = RFID.objects.create(
        rfid="ABCD1234",
        command_card_name="HEALTH SNAPSHOT",
        command_payload_digest=command_layout.command_payload_digest(
            name="HEALTH SNAPSHOT",
            command="SUITE_COMMAND",
            params={"command": "health", "args": [], "timeout": 0.01},
        ),
        command_provenance_key=metadata.provenance_key,
        owner_user=user,
    )

    def slow_call_command(*args, **kwargs):
        time.sleep(2)

    monkeypatch.setattr("apps.cards.card_commands.call_command", slow_call_command)

    execution = execute_command_card_payload(
        {"rfid": tag.rfid, "label_id": tag.pk, "dump": _dump_from_blocks(blocks)},
        reader_id="reader-1",
        result_writer=_writer,
    )

    assert execution.status == RFIDCommandExecution.Status.FAILED
    assert execution.result["summary"] == "health timed out"
    assert execution.result["payload"]["timeout"] == 1.0
