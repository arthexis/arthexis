from __future__ import annotations

import json
import logging
import sys
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.utils import OperationalError
from django.test import override_settings

from apps.meta import models as meta_models
from apps.meta.management.commands import whatsapp as whatsapp_command
from apps.meta.models import WhatsAppSecretaryAuthorizedPhone
from apps.meta.services import (
    WhatsAppSecretaryListenResult,
    WhatsAppWebLoginResult,
    WhatsAppWebLoginStatus,
    WhatsAppWebMessage,
    WhatsAppWebReadResult,
    WhatsAppWebSendResult,
    WhatsAppSecretaryAuthorizationUnavailable,
    _is_whatsapp_web_url,
    _read_cursor,
    _systemd_quote,
    _with_whatsapp_page,
    _write_cursor,
    build_whatsapp_secretary_prompt,
    build_whatsapp_web_messages,
    cursor_file_for_profile,
    cursor_key_for_profile,
    detect_whatsapp_web_login_state,
    filter_whatsapp_web_messages,
    launch_codex_secretary_terminal,
    listen_for_whatsapp_secretary_requests,
    normalize_whatsapp_phone,
    parse_cli_date,
    read_whatsapp_web_messages,
    registered_whatsapp_secretary_phones,
    secretary_request_text_from_messages,
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


def test_secretary_request_requires_trigger_and_keeps_continuations():
    messages = [
        WhatsAppWebMessage(
            fingerprint="a",
            index=0,
            message_id="a",
            direction="out",
            sender="ARTHEXIS",
            timestamp_raw="14:30, 2026-05-01",
            timestamp_iso="2026-05-01T14:30:00",
            text="ordinary self note",
        ),
        WhatsAppWebMessage(
            fingerprint="b",
            index=1,
            message_id="b",
            direction="out",
            sender="ARTHEXIS",
            timestamp_raw="14:31, 2026-05-01",
            timestamp_iso="2026-05-01T14:31:00",
            text="secretary: book the maintenance visit",
        ),
        WhatsAppWebMessage(
            fingerprint="c",
            index=2,
            message_id="c",
            direction="out",
            sender="ARTHEXIS",
            timestamp_raw="14:32, 2026-05-01",
            timestamp_iso="2026-05-01T14:32:00",
            text="include the charger photos",
        ),
    ]

    request = secretary_request_text_from_messages(messages, trigger_prefix="secretary:")
    prompt = build_whatsapp_secretary_prompt(messages, secretary_name="Mara")

    assert request == "book the maintenance visit\n\ninclude the charger photos"
    assert prompt.startswith("[SECRETARY] Mara:")
    assert "ordinary self note" not in prompt
    assert "book the maintenance visit" in prompt


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


def test_secretary_listener_waits_for_quiet_window_before_launch(tmp_path):
    profile_dir = tmp_path / "profile-a"
    messages = [
        WhatsAppWebMessage(
            fingerprint="a",
            index=0,
            message_id="a",
            direction="out",
            sender="ARTHEXIS",
            timestamp_raw="14:30, 2026-05-01",
            timestamp_iso="2026-05-01T14:30:00",
            text="secretary: summarize the quote",
        ),
        WhatsAppWebMessage(
            fingerprint="b",
            index=1,
            message_id="b",
            direction="out",
            sender="ARTHEXIS",
            timestamp_raw="14:31, 2026-05-01",
            timestamp_iso="2026-05-01T14:31:00",
            text="focus on pending approvals",
        ),
    ]
    reads = iter(
        [
            [messages[0]],
            messages,
            messages,
        ]
    )
    now = {"value": 0.0}
    launched_prompts = []

    def fake_read_messages(**kwargs):
        assert kwargs["only_new"] is True
        assert kwargs["update_cursor"] is False
        return WhatsAppWebReadResult(
            status="ok",
            phone="525551234567",
            profile_dir=profile_dir,
            messages=next(reads),
            cursor_file=cursor_file_for_profile(profile_dir),
        )

    def fake_sleep(seconds):
        now["value"] += seconds

    results = listen_for_whatsapp_secretary_requests(
        phone="5551234567",
        idle_after_seconds=0,
        daemon_poll_seconds=60,
        quiet_window_seconds=60,
        max_batches=1,
        read_messages=fake_read_messages,
        launch_callback=lambda prompt: launched_prompts.append(prompt) or "launched",
        monotonic=lambda: now["value"],
        sleep=fake_sleep,
        idle_seconds=lambda: 999,
        authorized_phones={"525551234567"},
    )
    cursor_key = cursor_key_for_profile("525551234567", profile_dir)

    assert results[-1].status == "launched"
    assert results[-1].message_count == 2
    assert len(launched_prompts) == 1
    assert "summarize the quote" in launched_prompts[0]
    assert "focus on pending approvals" in launched_prompts[0]
    assert _read_cursor(cursor_file_for_profile(profile_dir), cursor_key) == "b"


def test_secretary_listener_refreshes_authorized_phones_during_poll(tmp_path):
    profile_dir = tmp_path / "profile-a"
    message = WhatsAppWebMessage(
        fingerprint="a",
        index=0,
        message_id="a",
        direction="out",
        sender="You",
        timestamp_raw="14:30, 2026-05-01",
        timestamp_iso="2026-05-01T14:30:00",
        text="secretary: open a terminal",
    )
    calls = []
    now = {"value": 60.0}

    def fake_read_messages(**kwargs):
        del kwargs
        return WhatsAppWebReadResult(
            status="ok",
            phone="525551234567",
            profile_dir=profile_dir,
            messages=[message],
            cursor_file=cursor_file_for_profile(profile_dir),
        )

    def authorized_phones():
        calls.append("refresh")
        return {"525551234567"}

    results = listen_for_whatsapp_secretary_requests(
        phone="5551234567",
        idle_after_seconds=0,
        daemon_poll_seconds=60,
        quiet_window_seconds=60,
        max_batches=1,
        read_messages=fake_read_messages,
        launch=False,
        monotonic=lambda: now["value"],
        sleep=lambda seconds: now.update(value=now["value"] + seconds),
        idle_seconds=lambda: 999,
        authorized_phones=authorized_phones,
    )

    assert calls == ["refresh"]
    assert results[-1].status == "matched"


def test_secretary_listener_keeps_cursor_when_authorization_lookup_fails(tmp_path):
    profile_dir = tmp_path / "profile-a"
    message = WhatsAppWebMessage(
        fingerprint="a",
        index=0,
        message_id="a",
        direction="out",
        sender="You",
        timestamp_raw="14:30, 2026-05-01",
        timestamp_iso="2026-05-01T14:30:00",
        text="secretary: open a terminal",
    )
    now = {"value": 60.0}

    def fake_read_messages(**kwargs):
        del kwargs
        return WhatsAppWebReadResult(
            status="ok",
            phone="525551234567",
            profile_dir=profile_dir,
            messages=[message],
            cursor_file=cursor_file_for_profile(profile_dir),
        )

    def broken_authorized_phones():
        raise WhatsAppSecretaryAuthorizationUnavailable("lookup unavailable")

    results = listen_for_whatsapp_secretary_requests(
        phone="5551234567",
        idle_after_seconds=0,
        daemon_poll_seconds=60,
        quiet_window_seconds=60,
        max_batches=1,
        read_messages=fake_read_messages,
        launch=False,
        monotonic=lambda: now["value"],
        sleep=lambda seconds: now.update(value=now["value"] + seconds),
        idle_seconds=lambda: 999,
        authorized_phones=broken_authorized_phones,
    )
    cursor_key = cursor_key_for_profile("525551234567", profile_dir)

    assert results[-1].status == "authorization_unavailable"
    assert results[-1].cursor_updated is False
    assert _read_cursor(cursor_file_for_profile(profile_dir), cursor_key) == ""


def test_secretary_listener_ignores_and_advances_untriggered_batch(tmp_path):
    profile_dir = tmp_path / "profile-a"
    message = WhatsAppWebMessage(
        fingerprint="a",
        index=0,
        message_id="a",
        direction="out",
        sender="ARTHEXIS",
        timestamp_raw="14:30, 2026-05-01",
        timestamp_iso="2026-05-01T14:30:00",
        text="ordinary self note",
    )
    now = {"value": 60.0}

    def fake_read_messages(**kwargs):
        del kwargs
        return WhatsAppWebReadResult(
            status="ok",
            phone="525551234567",
            profile_dir=profile_dir,
            messages=[message],
            cursor_file=cursor_file_for_profile(profile_dir),
        )

    results = listen_for_whatsapp_secretary_requests(
        phone="5551234567",
        idle_after_seconds=0,
        daemon_poll_seconds=60,
        quiet_window_seconds=60,
        max_batches=1,
        read_messages=fake_read_messages,
        launch_callback=lambda prompt: f"unexpected {prompt}",
        monotonic=lambda: now["value"],
        sleep=lambda seconds: now.update(value=now["value"] + seconds),
        idle_seconds=lambda: 999,
        authorized_phones={"525551234567"},
    )
    cursor_key = cursor_key_for_profile("525551234567", profile_dir)

    assert results[-1].status == "ignored"
    assert results[-1].launched is False
    assert _read_cursor(cursor_file_for_profile(profile_dir), cursor_key) == "a"


def test_secretary_listener_keeps_cursor_when_launch_fails(tmp_path):
    profile_dir = tmp_path / "profile-a"
    message = WhatsAppWebMessage(
        fingerprint="a",
        index=0,
        message_id="a",
        direction="out",
        sender="ARTHEXIS",
        timestamp_raw="14:30, 2026-05-01",
        timestamp_iso="2026-05-01T14:30:00",
        text="secretary: open a terminal",
    )
    now = {"value": 60.0}

    def fake_read_messages(**kwargs):
        del kwargs
        return WhatsAppWebReadResult(
            status="ok",
            phone="525551234567",
            profile_dir=profile_dir,
            messages=[message],
            cursor_file=cursor_file_for_profile(profile_dir),
        )

    def fail_launch(prompt):
        raise RuntimeError(f"launch failed for {prompt[:10]}")

    with pytest.raises(RuntimeError, match="launch failed"):
        listen_for_whatsapp_secretary_requests(
            phone="5551234567",
            idle_after_seconds=0,
            daemon_poll_seconds=60,
            quiet_window_seconds=60,
            max_batches=1,
            read_messages=fake_read_messages,
            launch_callback=fail_launch,
            monotonic=lambda: now["value"],
            sleep=lambda seconds: now.update(value=now["value"] + seconds),
            idle_seconds=lambda: 999,
            authorized_phones={"525551234567"},
        )

    cursor_key = cursor_key_for_profile("525551234567", profile_dir)
    assert _read_cursor(cursor_file_for_profile(profile_dir), cursor_key) == ""


def test_secretary_launcher_preserves_windows_codex_path(monkeypatch, tmp_path):
    captured = {}

    def fake_launch_command_in_terminal(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return tmp_path / "terminal.pid"

    monkeypatch.setattr("apps.meta.services.sys.platform", "win32")
    monkeypatch.setattr(
        "apps.terminals.tasks.launch_command_in_terminal",
        fake_launch_command_in_terminal,
    )

    detail = launch_codex_secretary_terminal(
        "prompt",
        codex_command=r'"C:\Program Files\Codex\codex.exe" --model gpt-5',
    )

    assert captured["command"] == [
        r"C:\Program Files\Codex\codex.exe",
        "--model",
        "gpt-5",
        "prompt",
    ]
    assert captured["kwargs"]["state_key"] == "whatsapp-secretary"
    assert "terminal.pid" in detail


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


def test_whatsapp_listen_command_outputs_json(monkeypatch, tmp_path):
    def fake_listen_for_whatsapp_secretary_requests(**kwargs):
        assert kwargs["phone"] == "5551234567"
        assert kwargs["trigger_prefix"] == "secretary:"
        assert kwargs["idle_after_seconds"] == 300
        assert kwargs["daemon_poll_seconds"] == 60
        assert kwargs["quiet_window_seconds"] == 60
        assert kwargs["launch"] is False
        assert kwargs["max_batches"] == 1
        assert kwargs["profile_dir"] == tmp_path
        assert callable(kwargs["authorized_phones"])
        assert kwargs["authorized_phones"]() == {"525551234567"}
        return [
            WhatsAppSecretaryListenResult(
                status="matched",
                phone="525551234567",
                message_count=1,
                launched=False,
                cursor_updated=True,
                cursor_file=tmp_path / "message-cursors.json",
                batch_fingerprint="abc",
                detail="Secretary request matched.",
            )
        ]

    monkeypatch.setattr(
        whatsapp_command,
        "listen_for_whatsapp_secretary_requests",
        fake_listen_for_whatsapp_secretary_requests,
    )
    monkeypatch.setattr(whatsapp_command, "registered_whatsapp_secretary_phones", lambda **kwargs: {"525551234567"})
    stdout = StringIO()

    call_command(
        "whatsapp",
        "listen",
        "--from",
        "5551234567",
        "--profile-dir",
        str(tmp_path),
        "--once",
        "--no-launch",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["status"] == "matched"
    assert payload["message_count"] == 1


def test_whatsapp_install_listener_windows_writes_manual_artifacts(tmp_path):
    output_dir = tmp_path / "install"
    profile_dir = tmp_path / "profile"
    stdout = StringIO()

    with override_settings(BASE_DIR=tmp_path):
        call_command(
            "whatsapp",
            "install-listener",
            "--from",
            "5551234567",
            "--platform",
            "windows",
            "--output-dir",
            str(output_dir),
            "--profile-dir",
            str(profile_dir),
            "--codex-command",
            r"C:\Program Files\Codex\codex.exe",
            "--python",
            r"C:\Arthexis\.venv\Scripts\python.exe",
            "--manage-py",
            r"C:\Arthexis\manage.py",
            "--write",
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())
    runner_path = Path(payload["runner_path"])
    service_path = Path(payload["service_path"])

    assert payload["status"] == "written"
    assert payload["platform"] == "windows"
    assert payload["wrote_files"] is True
    assert runner_path.exists()
    assert service_path.exists()
    assert "--from" in payload["listen_command"]
    assert "5551234567" in payload["listen_command"]
    assert "C:\\Program Files\\Codex\\codex.exe" in payload["listen_command"]
    assert "Register-ScheduledTask" in service_path.read_text(encoding="utf-8")
    assert "whatsapp" in runner_path.read_text(encoding="utf-8")
    assert "listen" in runner_path.read_text(encoding="utf-8")


def test_whatsapp_install_listener_linux_dry_run_plans_systemd_user_unit(tmp_path):
    output_dir = tmp_path / "install"
    systemd_dir = tmp_path / "systemd-user"
    stdout = StringIO()

    with override_settings(BASE_DIR=tmp_path):
        call_command(
            "whatsapp",
            "install-listener",
            "--from",
            "5551234567",
            "--platform",
            "linux",
            "--output-dir",
            str(output_dir),
            "--systemd-user-dir",
            str(systemd_dir),
            "--python",
            "/opt/arthexis/.venv/bin/python",
            "--manage-py",
            "/opt/arthexis/manage.py",
            "--headless",
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())

    assert payload["status"] == "planned"
    assert payload["platform"] == "linux"
    assert payload["wrote_files"] is False
    assert payload["service_path"] == str(systemd_dir / "arthexis-whatsapp-listener.service")
    assert "systemctl --user enable" in payload["install_command"]
    assert str(systemd_dir / "arthexis-whatsapp-listener.service") in payload["install_command"]
    assert "--browser firefox" in payload["listen_command"]
    assert "--headless" in payload["listen_command"]
    assert not output_dir.exists()
    assert not systemd_dir.exists()


def test_whatsapp_install_listener_linux_write_restricts_runner_permissions(tmp_path):
    output_dir = tmp_path / "install"
    systemd_dir = tmp_path / "systemd-user"
    stdout = StringIO()

    with override_settings(BASE_DIR=tmp_path):
        call_command(
            "whatsapp",
            "install-listener",
            "--from",
            "5551234567",
            "--platform",
            "linux",
            "--output-dir",
            str(output_dir),
            "--systemd-user-dir",
            str(systemd_dir),
            "--python",
            "/opt/arthexis/.venv/bin/python",
            "--manage-py",
            "/opt/arthexis/manage.py",
            "--write",
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())
    runner_path = Path(payload["runner_path"])

    assert payload["status"] == "written"
    assert runner_path.exists()
    assert runner_path.stat().st_mode & 0o777 == 0o700


def test_install_listener_target_platform_controls_browser_defaults(tmp_path):
    stdout = StringIO()

    with override_settings(BASE_DIR=tmp_path):
        call_command(
            "whatsapp",
            "install-listener",
            "--from",
            "5551234567",
            "--platform",
            "windows",
            "--output-dir",
            str(tmp_path / "install"),
            "--python",
            r"C:\Arthexis\.venv\Scripts\python.exe",
            "--manage-py",
            r"C:\Arthexis\manage.py",
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())
    assert "--browser" in payload["listen_command"]
    assert "'edge'" in payload["listen_command"]
    assert "--channel" in payload["listen_command"]
    assert "'msedge'" in payload["listen_command"]


def test_install_listener_explicit_browser_skips_platform_channel_default(tmp_path):
    stdout = StringIO()

    with override_settings(BASE_DIR=tmp_path):
        call_command(
            "whatsapp",
            "install-listener",
            "--from",
            "5551234567",
            "--platform",
            "windows",
            "--browser",
            "firefox",
            "--output-dir",
            str(tmp_path / "install"),
            "--python",
            r"C:\Arthexis\.venv\Scripts\python.exe",
            "--manage-py",
            r"C:\Arthexis\manage.py",
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())
    assert "'firefox'" in payload["listen_command"]
    assert "--channel" not in payload["listen_command"]
    assert any("Playwright Firefox" in item for item in payload["requirements"])
    assert not any("Microsoft Edge" in item for item in payload["requirements"])


def test_install_listener_requirements_match_chromium_override(tmp_path):
    stdout = StringIO()

    with override_settings(BASE_DIR=tmp_path):
        call_command(
            "whatsapp",
            "install-listener",
            "--from",
            "5551234567",
            "--platform",
            "linux",
            "--browser",
            "chromium",
            "--output-dir",
            str(tmp_path / "install"),
            "--python",
            "/opt/arthexis/.venv/bin/python",
            "--manage-py",
            "/opt/arthexis/manage.py",
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())
    assert "--browser chromium" in payload["listen_command"]
    assert any("Playwright Chromium" in item for item in payload["requirements"])
    assert not any("Firefox" in item for item in payload["requirements"])


def test_install_listener_cross_platform_uses_target_path_defaults(tmp_path):
    target = "linux" if sys.platform == "win32" else "windows"
    stdout = StringIO()

    with override_settings(BASE_DIR=tmp_path):
        call_command(
            "whatsapp",
            "install-listener",
            "--from",
            "5551234567",
            "--platform",
            target,
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())
    assert sys.executable not in payload["listen_command"]
    assert str(tmp_path) not in payload["listen_command"]
    if target == "windows":
        assert payload["base_dir"] == r"C:\Arthexis"
        assert r"C:\Arthexis\.venv\Scripts\python.exe" in payload["listen_command"]
        assert r"C:\Arthexis\manage.py" in payload["listen_command"]
    else:
        assert payload["base_dir"] == "/opt/arthexis"
        assert "/opt/arthexis/.venv/bin/python" in payload["listen_command"]
        assert "/opt/arthexis/manage.py" in payload["listen_command"]


def test_install_listener_cross_platform_base_dir_controls_target_paths(tmp_path):
    target = "linux" if sys.platform == "win32" else "windows"
    base_dir = "/srv/arthexis" if target == "linux" else r"D:\Arthexis"
    stdout = StringIO()

    with override_settings(BASE_DIR=tmp_path):
        call_command(
            "whatsapp",
            "install-listener",
            "--from",
            "5551234567",
            "--platform",
            target,
            "--base-dir",
            base_dir,
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())
    assert payload["base_dir"] == base_dir
    if target == "windows":
        assert r"D:\Arthexis\.venv\Scripts\python.exe" in payload["listen_command"]
        assert r"D:\Arthexis\manage.py" in payload["listen_command"]
    else:
        assert "/srv/arthexis/.venv/bin/python" in payload["listen_command"]
        assert "/srv/arthexis/manage.py" in payload["listen_command"]


def test_install_listener_cross_platform_written_runner_uses_target_base_dir(tmp_path):
    target = "linux" if sys.platform == "win32" else "windows"
    output_dir = tmp_path / "install"
    stdout = StringIO()
    args = [
        "whatsapp",
        "install-listener",
        "--from",
        "5551234567",
        "--platform",
        target,
        "--output-dir",
        str(output_dir),
        "--write",
        "--json",
    ]
    if target == "linux":
        args.extend(["--systemd-user-dir", str(tmp_path / "systemd")])

    with override_settings(BASE_DIR=tmp_path):
        call_command(*args, stdout=stdout)

    payload = json.loads(stdout.getvalue())
    runner_text = Path(payload["runner_path"]).read_text(encoding="utf-8")
    assert str(tmp_path) not in payload["listen_command"]
    assert str(tmp_path) not in runner_text
    if target == "windows":
        assert r"C:\Arthexis" in runner_text
    else:
        assert "/opt/arthexis" in runner_text


def test_systemd_quote_escapes_backslashes_without_spaces():
    assert _systemd_quote(r"/tmp/with\backslash") == r'"/tmp/with\\backslash"'


def test_whatsapp_install_listener_rejects_bad_timing():
    with pytest.raises(CommandError, match="--quiet-window must be >= 1"):
        call_command(
            "whatsapp",
            "install-listener",
            "--from",
            "5551234567",
            "--quiet-window",
            "0",
        )


def test_secretary_request_ignores_non_authorized_senders():
    messages = [
        WhatsAppWebMessage(
            fingerprint="a",
            index=0,
            message_id="a",
            direction="in",
            sender="15551234567",
            timestamp_raw="14:30, 2026-05-01",
            timestamp_iso="2026-05-01T14:30:00",
            text="secretary: do not run",
        ),
        WhatsAppWebMessage(
            fingerprint="b",
            index=1,
            message_id="b",
            direction="out",
            sender="+52 55 5123 4567",
            timestamp_raw="14:31, 2026-05-01",
            timestamp_iso="2026-05-01T14:31:00",
            text="secretary: run this",
        ),
    ]

    request = secretary_request_text_from_messages(
        messages,
        trigger_prefix="secretary:",
        authorized_phones={"525551234567"},
    )

    assert request == "run this"


def test_secretary_request_uses_fallback_phone_for_display_sender():
    messages = [
        WhatsAppWebMessage(
            fingerprint="a",
            index=0,
            message_id="a",
            direction="out",
            sender="You",
            timestamp_raw="14:30, 2026-05-01",
            timestamp_iso="2026-05-01T14:30:00",
            text="secretary: run alias",
        ),
    ]

    request = secretary_request_text_from_messages(
        messages,
        trigger_prefix="secretary:",
        authorized_phones={"525551234567"},
        fallback_sender_phone="5551234567",
    )

    assert request == "run alias"


def test_secretary_request_rejects_display_sender_without_fallback_phone():
    messages = [
        WhatsAppWebMessage(
            fingerprint="a",
            index=0,
            message_id="a",
            direction="out",
            sender="You",
            timestamp_raw="14:30, 2026-05-01",
            timestamp_iso="2026-05-01T14:30:00",
            text="secretary: run alias",
        ),
    ]

    request = secretary_request_text_from_messages(
        messages,
        trigger_prefix="secretary:",
        authorized_phones={"525551234567"},
    )

    assert request == ""


def test_secretary_request_rejects_inbound_display_sender_with_fallback_phone():
    messages = [
        WhatsAppWebMessage(
            fingerprint="a",
            index=0,
            message_id="a",
            direction="in",
            sender="Alice",
            timestamp_raw="14:30, 2026-05-01",
            timestamp_iso="2026-05-01T14:30:00",
            text="secretary: run alias",
        ),
    ]

    request = secretary_request_text_from_messages(
        messages,
        trigger_prefix="secretary:",
        authorized_phones={"525551234567"},
        fallback_sender_phone="5551234567",
    )

    assert request == ""


def test_whatsapp_register_phone_command_creates_authorization(monkeypatch):
    class User:
        def __str__(self):
            return "ops"

    class Query:
        def __init__(self, user):
            self.user = user

        def first(self):
            return self.user

    class UserManager:
        def filter(self, *args, **kwargs):
            del args, kwargs
            return Query(User())

    class PhoneManager:
        def __init__(self):
            self.kwargs = {}

        def update_or_create(self, **kwargs):
            self.kwargs = kwargs
            auth = type("Auth", (), {"phone": kwargs["phone"], "label": kwargs["defaults"]["label"]})()
            return auth, True

    manager = PhoneManager()
    monkeypatch.setattr(whatsapp_command, "get_user_model", lambda: type("U", (), {"objects": UserManager()})())
    monkeypatch.setattr(whatsapp_command, "WhatsAppSecretaryAuthorizedPhone", type("P", (), {"objects": manager}))

    stdout = StringIO()
    call_command("whatsapp", "register-phone", "--user", "ops", "--phone", "5551234567", "--json", stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["status"] == "created"
    assert manager.kwargs["phone"] == "525551234567"
    assert manager.kwargs["defaults"]["is_deleted"] is False


def test_whatsapp_register_phone_command_rejects_sender_alias(monkeypatch):
    class User:
        def __str__(self):
            return "ops"

    class Query:
        def first(self):
            return User()

    class UserManager:
        def filter(self, *args, **kwargs):
            del args, kwargs
            return Query()

    class PhoneManager:
        def __init__(self):
            self.kwargs = {}

        def update_or_create(self, **kwargs):
            self.kwargs = kwargs
            auth = type("Auth", (), {"phone": kwargs["phone"], "label": kwargs["defaults"]["label"]})()
            return auth, False

    manager = PhoneManager()
    monkeypatch.setattr(whatsapp_command, "get_user_model", lambda: type("U", (), {"objects": UserManager()})())
    monkeypatch.setattr(whatsapp_command, "WhatsAppSecretaryAuthorizedPhone", type("P", (), {"objects": manager}))

    stdout = StringIO()
    with pytest.raises(CommandError, match="valid WhatsApp phone number"):
        call_command(
            "whatsapp",
            "register-phone",
            "--user",
            "ops",
            "--phone",
            "You",
            "--label",
            "self-chat",
            "--json",
            stdout=stdout,
        )
    assert manager.kwargs == {}


@pytest.mark.django_db
def test_registered_whatsapp_secretary_phones_excludes_soft_deleted(django_user_model):
    user = django_user_model.objects.create_user(username="ops")
    WhatsAppSecretaryAuthorizedPhone.all_objects.create(
        user=user,
        phone="525551234567",
        is_active=True,
    )
    WhatsAppSecretaryAuthorizedPhone.all_objects.create(
        user=user,
        phone="525551234568",
        is_active=True,
        is_deleted=True,
    )
    WhatsAppSecretaryAuthorizedPhone.all_objects.create(
        user=user,
        phone="525551234569",
        is_active=False,
    )

    assert registered_whatsapp_secretary_phones() == {"525551234567"}


def test_registered_whatsapp_secretary_phones_handles_lazy_queryset_errors(monkeypatch):
    class BrokenQuerySet:
        def __iter__(self):
            raise OperationalError("missing authorization table")

    class Manager:
        def filter(self, **kwargs):
            del kwargs
            return BrokenQuerySet()

    monkeypatch.setattr(
        meta_models,
        "WhatsAppSecretaryAuthorizedPhone",
        type("P", (), {"objects": Manager()}),
    )

    assert registered_whatsapp_secretary_phones() == set()


def test_registered_whatsapp_secretary_phones_can_raise_lazy_queryset_errors(monkeypatch):
    class BrokenQuerySet:
        def __iter__(self):
            raise OperationalError("missing authorization table")

    class Manager:
        def filter(self, **kwargs):
            del kwargs
            return BrokenQuerySet()

    monkeypatch.setattr(
        meta_models,
        "WhatsAppSecretaryAuthorizedPhone",
        type("P", (), {"objects": Manager()}),
    )

    with pytest.raises(WhatsAppSecretaryAuthorizationUnavailable):
        registered_whatsapp_secretary_phones(raise_on_error=True)
