from importlib import import_module


def test_populate_videodevice_names_is_noop():
    """The legacy video-device migration hook should remain callable as a no-op."""

    migration = import_module("apps.video.migrations.0006_videodevice_name_slug")

    migration.populate_videodevice_names(apps=None, schema_editor=None)
