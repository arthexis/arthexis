from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.meta.management.commands import whatsapp as whatsapp_command
from apps.meta.services import (
    WhatsAppWebLoginResult,
    WhatsAppWebLoginStatus,
    WhatsAppWebMessage,
    WhatsAppWebReadResult,
    WhatsAppWebSendResult,
    _is_whatsapp_web_url,
    _read_cursor,
    _with_whatsapp_page,
    _write_cursor,
    build_whatsapp_web_messages,
    cursor_file_for_profile,
    cursor_key_for_profile,
    detect_whatsapp_web_login_state,
    filter_whatsapp_web_messages,
    normalize_whatsapp_phone,
    parse_cli_date,
    read_whatsapp_web_messages,
)


class FakeLocator:
    def __init__(self, visible: bool):
        self._visible = visible
        self.first = self
        self.last = self

    def is_visible(self, *, timeout: int):
        del timeout
        return self._visible

    def wait_for(self, *, timeout: int):
        del timeout
        return None


class FakePage:
    def __init__(self, *, visible_selectors=None, visible_texts=None, rows=None):
        self.visible_selectors = set(visible_selectors or [])
        self.visible_texts = set(visible_texts or [])
        self.rows = list(rows or [])
        self.url = ""

    def locator(self, selector: str):
        return FakeLocator(selector in self.visible_selectors)

    def get_by_text(self, text: str, *, exact: bool):
        del exact
        return FakeLocator(text in self.visible_texts)

    def goto(self, url: str, *, wait_until: str, timeout: int):
        del wait_until, timeout
        self.url = url

    def evaluate(self, script: str):
        del script
        return self.rows


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
    assert normalize_whatsapp_phone("555 123 4567") == "525551234567"
    assert normalize_whatsapp_phone("555 123 4567", default_country_code="+52") == "525551234567"
    assert normalize_whatsapp_phone("+1 (555) 123-4567") == "15551234567"


def test_is_whatsapp_web_url_requires_exact_whatsapp_host():
    assert _is_whatsapp_web_url("https://web.whatsapp.com/")
    assert _is_whatsapp_web_url("http://web.whatsapp.com/send?phone=525551234567")
    assert not _is_whatsapp_web_url("https://example.com/?next=https://web.whatsapp.com/")
    assert not _is_whatsapp_web_url("https://web.whatsapp.com.example.com/")
    assert not _is_whatsapp_web_url("notaurl")


def test_cdp_url_rejects_firefox_browser():
    with pytest.raises(ValueError, match="Chromium/Edge"):
        _with_whatsapp_page(
            lambda *_args: None,
            browser="firefox",
            cdp_url="http://127.0.0.1:9223",
            timeout_seconds=1,
        )


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
            "message_id": "message-a",
            "text": "hello",
        },
        {
            "pre": "[14:31, 2026-05-01] You: ",
            "direction": "out",
            "message_id": "message-b",
            "text": "reply",
        },
    ]

    messages = build_whatsapp_web_messages(rows, phone="525551234567")

    assert [message.text for message in messages] == ["hello", "reply"]
    assert messages[0].timestamp_iso == "2026-05-01T14:30:00"
    assert messages[0].sender == "ARTHEXIS"
    assert messages[0].message_id == "message-a"
    assert messages[0].fingerprint


def test_build_messages_fingerprint_is_stable_across_dom_positions():
    row = {
        "pre": "[14:30, 2026-05-01] ARTHEXIS: ",
        "direction": "in",
        "message_id": "stable-message-id",
        "text": "hello",
    }
    first = build_whatsapp_web_messages([row], phone="525551234567")[0]
    second = build_whatsapp_web_messages(
        [
            {"pre": "", "direction": "in", "text": ""},
            row,
        ],
        phone="525551234567",
    )[0]

    assert first.index == 0
    assert second.index == 1
    assert first.fingerprint == second.fingerprint


def test_build_messages_logs_unparseable_timestamp(caplog):
    rows = [
        {
            "pre": "[not a supported timestamp] ARTHEXIS: ",
            "direction": "in",
            "message_id": "message-a",
            "text": "hello",
        }
    ]

    with caplog.at_level(logging.WARNING, logger="apps.meta.services"):
        messages = build_whatsapp_web_messages(rows, phone="525551234567")

    assert messages[0].timestamp_iso == ""
    assert "Could not parse WhatsApp Web timestamp" in caplog.text


def test_filter_messages_by_date_and_cursor():
    first = WhatsAppWebMessage(
        fingerprint="a",
        index=0,
        message_id="a",
        direction="in",
        sender="A",
        timestamp_raw="14:30, 2026-05-01",
        timestamp_iso="2026-05-01T14:30:00",
        text="old",
    )
    second = WhatsAppWebMessage(
        fingerprint="b",
        index=1,
        message_id="b",
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


def test_cursor_file_round_trips_atomic_payload(tmp_path):
    cursor_file = tmp_path / "message-cursors.json"

    _write_cursor(cursor_file, "525551234567:default", "fingerprint-a")
    _write_cursor(cursor_file, "525551234568:default", "fingerprint-b")

    payload = json.loads(cursor_file.read_text(encoding="utf-8"))
    assert payload["expires_at"] is None
    assert payload["cursors"]["525551234567:default"] == "fingerprint-a"
    assert payload["cursors"]["525551234568:default"] == "fingerprint-b"
    assert _read_cursor(cursor_file, "525551234567:default") == "fingerprint-a"


def test_cursor_key_is_scoped_to_profile_path(tmp_path):
    first = cursor_key_for_profile("525551234567", tmp_path / "profile-a")
    second = cursor_key_for_profile("525551234567", tmp_path / "profile-b")

    assert first.startswith("525551234567:")
    assert second.startswith("525551234567:")
    assert first != second


def test_read_new_cursor_advances_to_returned_limited_batch(monkeypatch, tmp_path):
    rows = [
        {
            "pre": f"[14:{minute:02d}, 2026-05-01] ARTHEXIS: ",
            "direction": "in",
            "message_id": f"message-{index}",
            "text": f"message {index}",
        }
        for index, minute in enumerate(range(30, 34))
    ]
    profile_dir = tmp_path / "profile-a"

    def fake_with_whatsapp_page(callback, **kwargs):
        del kwargs
        page = FakePage(visible_selectors={"#pane-side"}, rows=rows)
        return callback(page, profile_dir, 1.0)

    monkeypatch.setattr(
        "apps.meta.services._with_whatsapp_page",
        fake_with_whatsapp_page,
    )

    result = read_whatsapp_web_messages(
        phone="5551234567",
        only_new=True,
        limit=2,
        profile_dir=profile_dir,
        timeout_seconds=1.0,
    )
    all_messages = build_whatsapp_web_messages(rows, phone=result.phone)
    cursor_file = cursor_file_for_profile(profile_dir)
    cursor_key = cursor_key_for_profile(result.phone, profile_dir)

    assert [message.text for message in result.messages] == ["message 0", "message 1"]
    assert result.cursor_updated is True
    assert _read_cursor(cursor_file, cursor_key) == result.messages[-1].fingerprint
    assert result.messages[-1].fingerprint != all_messages[-1].fingerprint


def test_whatsapp_login_command_outputs_json(monkeypatch, tmp_path):
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
        "login",
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
        assert kwargs["phone"] == "5551234567"
        assert kwargs["message"] == "hello"
        assert kwargs["default_country_code"] == "52"
        return WhatsAppWebSendResult(
            status="sent",
            phone="525551234567",
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
        "5551234567",
        "--message",
        "hello",
        "--profile-dir",
        str(tmp_path),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["status"] == "sent"
    assert payload["phone"] == "525551234567"


def test_whatsapp_read_command_outputs_messages(monkeypatch, tmp_path):
    def fake_read_whatsapp_web_messages(**kwargs):
        assert kwargs["phone"] == "5551234567"
        assert kwargs["since"].isoformat() == "2026-05-01"
        assert kwargs["until"].isoformat() == "2026-05-01"
        assert kwargs["only_new"] is True
        return WhatsAppWebReadResult(
            status="ok",
            phone="525551234567",
            profile_dir=tmp_path,
            messages=[
                WhatsAppWebMessage(
                    fingerprint="abc",
                    index=1,
                    message_id="abc",
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
        "5551234567",
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


def test_whatsapp_read_command_rejects_negative_limit():
    with pytest.raises(CommandError, match="--limit must be >= 0"):
        call_command(
            "whatsapp",
            "read",
            "--from",
            "5551234567",
            "--limit",
            "-1",
        )
