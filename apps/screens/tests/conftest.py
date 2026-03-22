import pytest

from apps.screens import lcd_screen


@pytest.fixture(autouse=True)
def mock_suite_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lcd_screen, "_suite_reachable", lambda *args, **kwargs: False)


@pytest.fixture(autouse=True)
def mock_lcd_feature_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default LCD command feature gate checks to active for command tests."""

    monkeypatch.setattr(
        "apps.screens.management.commands.lcd_actions.calibrate.is_local_node_feature_active",
        lambda slug: True,
    )
    monkeypatch.setattr(
        "apps.screens.management.commands.lcd_actions.write.is_local_node_feature_active",
        lambda slug: True,
    )
