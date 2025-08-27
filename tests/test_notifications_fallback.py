from msg import notifications


def test_gui_display_uses_plyer_when_available(monkeypatch):
    class FakePlyer:
        def __init__(self):
            self.calls = []

        def notify(self, **kwargs):
            self.calls.append(kwargs)

    fake = FakePlyer()
    monkeypatch.setattr(notifications, "plyer_notification", fake)

    nm = notifications.NotificationManager()
    nm.lcd = None
    nm._gui_display("subject", "body")

    assert fake.calls[0]["title"] == "Arthexis"
    assert fake.calls[0]["timeout"] == 6


def test_gui_display_logs_when_plyer_unavailable(monkeypatch, caplog):
    monkeypatch.setattr(notifications, "plyer_notification", None)
    nm = notifications.NotificationManager()
    nm.lcd = None

    with caplog.at_level("INFO"):
        nm._gui_display("subject", "body")

    assert "subject body" in caplog.text


def test_send_returns_true_on_notification_failure(monkeypatch):
    class BadPlyer:
        def notify(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(notifications, "plyer_notification", BadPlyer())

    nm = notifications.NotificationManager()
    nm.lcd = None

    assert nm.send("subject", "body") is True
