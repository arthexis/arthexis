from msg import notifications


def test_gui_display_uses_toast_when_available(monkeypatch):
    class FakeToaster:
        def __init__(self):
            self.calls = []

        def show_toast(self, title, message, duration=5, **kwargs):
            self.calls.append((title, message, duration, kwargs))

    fake = FakeToaster()
    monkeypatch.setattr(notifications, "ToastNotifier", lambda: fake)
    monkeypatch.setattr(notifications, "plyer_notification", None)
    monkeypatch.setattr(notifications.sys, "platform", "win32")

    nm = notifications.NotificationManager()
    nm._gui_display("subject", "body")

    assert fake.calls[0][0] == "Arthexis"
    assert fake.calls[0][2] == 6


def test_gui_display_uses_plyer_when_toast_unavailable(monkeypatch):
    class FakePlyer:
        def __init__(self):
            self.calls = []

        def notify(self, **kwargs):
            self.calls.append(kwargs)

    fake = FakePlyer()
    monkeypatch.setattr(notifications, "ToastNotifier", None)
    monkeypatch.setattr(notifications, "plyer_notification", fake)
    monkeypatch.setattr(notifications.sys, "platform", "win32")

    nm = notifications.NotificationManager()
    nm._gui_display("subject", "body")

    assert fake.calls[0]["title"] == "Arthexis"
    assert fake.calls[0]["timeout"] == 6


def test_send_returns_true_and_disables_toaster_on_failure(monkeypatch, tmp_path):
    class BadToaster:
        def show_toast(self, *args, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(notifications, "ToastNotifier", lambda: BadToaster())
    monkeypatch.setattr(notifications, "plyer_notification", None)
    monkeypatch.setattr(notifications.sys, "platform", "win32")

    lock = tmp_path / "lcd_screen.lck"
    lock.touch()
    nm = notifications.NotificationManager(lock_file=lock)

    monkeypatch.setattr(
        nm, "_write_lock_file", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    assert nm.send("subject", "body") is True
    assert nm._toaster is None


def test_send_uses_gui_when_lock_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(notifications.sys, "platform", "win32")
    lock = tmp_path / "lcd_screen.lck"  # do not create
    nm = notifications.NotificationManager(lock_file=lock)
    calls = []
    nm._gui_display = lambda s, b: calls.append((s, b))

    assert nm.send("subject", "body") is True
    assert calls == [("subject", "body")]
