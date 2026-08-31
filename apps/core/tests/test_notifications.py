from __future__ import annotations

from apps.core.notifications import LcdChannel, NotificationManager


def test_notification_manager_rejects_traversal_channel_type(tmp_path):
    manager = NotificationManager(lock_dir=tmp_path / ".locks")

    target = manager.get_target_lock_file(
        channel_type="x/../../../tmp/watchlist-pwn",
        channel_num=None,
    )

    assert target == tmp_path / ".locks" / "lcd-low"
    assert target.resolve().parent == manager.lock_dir.resolve()


def test_notification_manager_preserves_safe_custom_channel_type(tmp_path):
    manager = NotificationManager(lock_dir=tmp_path / ".locks")

    target = manager.get_target_lock_file(
        channel_type="custom_panel",
        channel_num=3,
    )

    assert target == tmp_path / ".locks" / "lcd-custom_panel-3"


def test_notification_manager_maps_full_channel_alias_to_event():
    assert NotificationManager._normalize_channel_type("full") == LcdChannel.EVENT.value
