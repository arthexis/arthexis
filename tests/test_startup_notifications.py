from nodes import startup_notifications as sn


def test_build_startup_message_trims_whitespace(monkeypatch, tmp_path):
    version_file = tmp_path / "VERSION"
    version_file.write_text(" v1.2.3 \n", encoding="utf-8")

    monkeypatch.setenv("PORT", " 9999 ")
    monkeypatch.setattr(sn.socket, "gethostname", lambda: " demo-host ")
    monkeypatch.setattr(sn.revision, "get_revision", lambda: " abcdef123456 ")

    subject, body = sn.build_startup_message(tmp_path, allow_db_lookup=False)

    assert subject == "demo-host:9999"
    assert body == "v1.2.3 123456"


def test_render_lcd_payload_trims_lines():
    payload = sn.render_lcd_payload(" subject  ", " body  ", scroll_ms=250)

    lines = payload.splitlines()

    assert lines[0] == "subject"
    assert lines[1] == "body"
    assert lines[2] == "250"
