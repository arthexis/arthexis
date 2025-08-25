from nodes import notifications


def test_gui_display_uses_toast_when_available(monkeypatch):
    class FakeToaster:
        def __init__(self):
            self.calls = []

        def show_toast(self, title, message, duration, threaded):
            self.calls.append((title, message, duration, threaded))

    fake = FakeToaster()
    monkeypatch.setattr(notifications, "ToastNotifier", lambda: fake)
    monkeypatch.setattr(notifications, "plyer_notification", None)
    monkeypatch.setattr(notifications.sys, "platform", "win32")

    nm = notifications.NotificationManager()
    nm.lcd = None
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
    nm.lcd = None
    nm._gui_display("subject", "body")

    assert fake.calls[0]["title"] == "Arthexis"
    assert fake.calls[0]["timeout"] == 6
