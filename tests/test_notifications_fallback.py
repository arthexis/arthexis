import pytest

from core import notifications


pytestmark = [
    pytest.mark.role("Terminal"),
    pytest.mark.role("Control"),
    pytest.mark.feature("gui-toast"),
]


def test_gui_display_uses_plyer_when_available(monkeypatch):
    class FakePlyer:
        def __init__(self):
            self.calls = []

        def notify(self, **kwargs):
            self.calls.append(kwargs)

    fake = FakePlyer()
    monkeypatch.setattr(notifications, "plyer_notification", fake)
    monkeypatch.setattr(notifications.sys, "platform", "win32")

    nm = notifications.NotificationManager()
    nm._gui_display("subject", "body")

    assert fake.calls[0]["title"] == "Arthexis"
    assert fake.calls[0]["timeout"] == 6


def test_supports_gui_toast_true_on_windows(monkeypatch):
    class FakePlyer:
        def notify(self, **kwargs):
            return None

    monkeypatch.setattr(notifications.sys, "platform", "win32")
    monkeypatch.setattr(notifications, "plyer_notification", FakePlyer())

    assert notifications.supports_gui_toast() is True


def test_supports_gui_toast_false_when_missing_plyer(monkeypatch):
    monkeypatch.setattr(notifications.sys, "platform", "win32")
    monkeypatch.setattr(notifications, "plyer_notification", None)

    assert notifications.supports_gui_toast() is False


def test_send_returns_true_on_gui_failure(monkeypatch, tmp_path):
    class BadPlyer:
        def notify(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(notifications, "plyer_notification", BadPlyer())
    monkeypatch.setattr(notifications.sys, "platform", "win32")
    lock = tmp_path / "lcd_screen.lck"
    lock.touch()
    nm = notifications.NotificationManager(lock_file=lock)

    monkeypatch.setattr(
        nm,
        "_write_lock_file",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert nm.send("subject", "body") is True


def test_send_uses_gui_when_lock_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(notifications.sys, "platform", "win32")
    lock = tmp_path / "lcd_screen.lck"  # do not create
    nm = notifications.NotificationManager(lock_file=lock)
    calls = []
    nm._gui_display = lambda s, b: calls.append((s, b))

    assert nm.send("subject", "body") is True
    assert calls == [("subject", "body")]
