from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.meta.management.commands import whatsapp as whatsapp_command
from apps.meta.services import (
    WhatsAppWebLoginResult,
    WhatsAppWebLoginStatus,
    WhatsAppWebMessage,
    WhatsAppWebReadResult,
    WhatsAppWebSendResult,
    build_whatsapp_web_messages,
    cursor_file_for_profile,
    detect_whatsapp_web_login_state,
    filter_whatsapp_web_messages,
    normalize_whatsapp_phone,
    parse_cli_date,
)


class FakeLocator:
    def __init__(self, visible: bool):
        self._visible = visible
        self.first = self

    def is_visible(self, *, timeout: int):
        del timeout
        return self._visible


class FakePage:
    def __init__(self, *, visible_selectors=None, visible_texts=None):
        self.visible_selectors = set(visible_selectors or [])
        self.visible_texts = set(visible_texts or [])

    def locator(self, selector: str):
        return FakeLocator(selector in self.visible_selectors)

    def get_by_text(self, text: str, *, exact: bool):
        del exact
        return FakeLocator(text in self.visible_texts)


def test_detect_whatsapp_web_login_state_detects_logged_in_chat_list():
    page = FakePage(visible_selectors={"#pane-side", "[data-testid='qrcode']"})

    status, marker = detect_whatsapp_web_login_state(page)

    assert status == WhatsAppWebLoginStatus.LOGGED_IN
    assert marker == "#pane-side"


def test_detect_whatsapp_web_login_state_detects_login_required_qr():
    page = FakePage(visible_selectors={"[data-testid='qrcode']"})

    status, marker = detect_whatsapp_web_login_state(page)

    assert status == WhatsAppWebLoginStatus.LOGIN_REQUIRED
    assert marker == "[data-testid='qrcode']"


def test_detect_whatsapp_web_login_state_detects_login_required_text():
    page = FakePage(visible_texts={"Use WhatsApp on your computer"})

    status, marker = detect_whatsapp_web_login_state(page)

    assert status == WhatsAppWebLoginStatus.LOGIN_REQUIRED
    assert marker == "Use WhatsApp on your computer"


def test_normalize_whatsapp_phone_adds_default_country_code():
    assert normalize_whatsapp_phone("811 921 8587") == "528119218587"
    assert normalize_whatsapp_phone("+1 (555) 123-4567") == "15551234567"


def test_parse_cli_date_requires_iso_format():
    assert parse_cli_date("2026-05-01").isoformat() == "2026-05-01"
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_cli_date("01/05/2026")


def test_cursor_file_for_profile_uses_profile_parent(tmp_path):
    profile = tmp_path / "profile"

    assert cursor_file_for_profile(profile) == tmp_path / "message-cursors.json"


def test_build_messages_from_whatsapp_rows():
    rows = [
        {
            "pre": "[14:30, 2026-05-01] ARTHEXIS: ",
            "direction": "in",
            "text": "hello",
        },
        {
            "pre": "[14:31, 2026-05-01] You: ",
            "direction": "out",
            "text": "reply",
        },
    ]

    messages = build_whatsapp_web_messages(rows, phone="528119218587")

    assert [message.text for message in messages] == ["hello", "reply"]
    assert messages[0].timestamp_iso == "2026-05-01T14:30:00"
    assert messages[0].sender == "ARTHEXIS"
    assert messages[0].fingerprint


def test_filter_messages_by_date_and_cursor():
    first = WhatsAppWebMessage(
        fingerprint="a",
        index=0,
        direction="in",
        sender="A",
        timestamp_raw="14:30, 2026-05-01",
        timestamp_iso="2026-05-01T14:30:00",
        text="old",
    )
    second = WhatsAppWebMessage(
        fingerprint="b",
        index=1,
        direction="in",
        sender="A",
        timestamp_raw="14:30, 2026-05-02",
        timestamp_iso="2026-05-02T14:30:00",
        text="new",
    )

    filtered = filter_whatsapp_web_messages(
        [first, second],
        since=parse_cli_date("2026-05-02"),
        until=parse_cli_date("2026-05-02"),
        after_fingerprint="a",
    )

    assert filtered == [second]


def test_whatsapp_status_command_outputs_json(monkeypatch, tmp_path):
    def fake_validate_whatsapp_web_login(**kwargs):
        assert kwargs["profile_dir"] == tmp_path
        assert kwargs["timeout_seconds"] == 3
        assert kwargs["poll_interval_seconds"] == 0.5
        assert kwargs["headless"] is True
        assert kwargs["browser"] == "edge"
        assert kwargs["channel"] == "msedge"
        return WhatsAppWebLoginResult(
            status=WhatsAppWebLoginStatus.LOGIN_REQUIRED,
            profile_dir=Path(tmp_path),
            elapsed_seconds=3.01,
            url="https://web.whatsapp.com/",
            marker="[data-testid='qrcode']",
            detail="WhatsApp Web login screen was visible until timeout.",
        )

    monkeypatch.setattr(
        whatsapp_command,
        "validate_whatsapp_web_login",
        fake_validate_whatsapp_web_login,
    )
    stdout = StringIO()

    call_command(
        "whatsapp",
        "status",
        "--profile-dir",
        str(tmp_path),
        "--timeout",
        "3",
        "--poll-interval",
        "0.5",
        "--headless",
        "--browser",
        "edge",
        "--channel",
        "msedge",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["status"] == WhatsAppWebLoginStatus.LOGIN_REQUIRED
    assert payload["profile_dir"] == str(tmp_path)
    assert payload["marker"] == "[data-testid='qrcode']"


def test_whatsapp_send_command_outputs_json(monkeypatch, tmp_path):
    def fake_send_whatsapp_web_message(**kwargs):
        assert kwargs["phone"] == "8119218587"
        assert kwargs["message"] == "hello"
        assert kwargs["default_country_code"] == "52"
        return WhatsAppWebSendResult(
            status="sent",
            phone="528119218587",
            profile_dir=tmp_path,
            elapsed_seconds=1.2,
            url="https://web.whatsapp.com/",
            detail="Message submitted through WhatsApp Web.",
        )

    monkeypatch.setattr(
        whatsapp_command,
        "send_whatsapp_web_message",
        fake_send_whatsapp_web_message,
    )
    stdout = StringIO()

    call_command(
        "whatsapp",
        "send",
        "--to",
        "8119218587",
        "--message",
        "hello",
        "--profile-dir",
        str(tmp_path),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["status"] == "sent"
    assert payload["phone"] == "528119218587"


def test_whatsapp_read_command_outputs_messages(monkeypatch, tmp_path):
    def fake_read_whatsapp_web_messages(**kwargs):
        assert kwargs["phone"] == "8119218587"
        assert kwargs["since"].isoformat() == "2026-05-01"
        assert kwargs["until"].isoformat() == "2026-05-01"
        assert kwargs["only_new"] is True
        return WhatsAppWebReadResult(
            status="ok",
            phone="528119218587",
            profile_dir=tmp_path,
            messages=[
                WhatsAppWebMessage(
                    fingerprint="abc",
                    index=1,
                    direction="in",
                    sender="ARTHEXIS",
                    timestamp_raw="14:30, 2026-05-01",
                    timestamp_iso="2026-05-01T14:30:00",
                    text="hello",
                )
            ],
            cursor_updated=True,
            cursor_file=tmp_path / "message-cursors.json",
        )

    monkeypatch.setattr(
        whatsapp_command,
        "read_whatsapp_web_messages",
        fake_read_whatsapp_web_messages,
    )
    stdout = StringIO()

    call_command(
        "whatsapp",
        "read",
        "--from",
        "8119218587",
        "--date",
        "2026-05-01",
        "--new",
        "--profile-dir",
        str(tmp_path),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["status"] == "ok"
    assert payload["messages"][0]["text"] == "hello"
    assert payload["cursor_updated"] is True
