import pytest

from apps.screens import lcd_screen


@pytest.fixture(autouse=True)
def mock_suite_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lcd_screen, "_suite_reachable", lambda *args, **kwargs: False)
