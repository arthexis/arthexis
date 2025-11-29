import logging

from core import lcd_screen


def test_handle_lcd_failure_disables_feature(tmp_path, caplog):
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    feature_lock = lock_dir / lcd_screen.FEATURE_LOCK_NAME
    runtime_lock = lock_dir / lcd_screen.LOCK_FILE.name
    feature_lock.touch()
    runtime_lock.touch()

    exc = FileNotFoundError(2, "No such file or directory", "/dev/i2c-1")
    with caplog.at_level(logging.WARNING):
        should_disable = lcd_screen._handle_lcd_failure(exc, lock_dir)

    assert should_disable is True
    assert not feature_lock.exists()
    assert not runtime_lock.exists()
    assert any(
        "disabling lcd-screen feature" in message for message in caplog.messages
    )


def test_handle_lcd_failure_leaves_other_errors(tmp_path, caplog):
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    feature_lock = lock_dir / lcd_screen.FEATURE_LOCK_NAME
    runtime_lock = lock_dir / lcd_screen.LOCK_FILE.name
    feature_lock.touch()
    runtime_lock.touch()

    with caplog.at_level(logging.WARNING):
        should_disable = lcd_screen._handle_lcd_failure(RuntimeError("boom"), lock_dir)

    assert should_disable is False
    assert feature_lock.exists()
    assert runtime_lock.exists()
    assert any("LCD update failed" in message for message in caplog.messages)
