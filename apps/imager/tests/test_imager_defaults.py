from apps.imager.constants import DEFAULT_RPI4B_BASE_IMAGE_URI
from apps.imager.management.commands.imager import Command


def test_build_defaults_to_raspberry_pi_os_lite_arm64():
    """Verify that imager build command defaults to the standard Raspberry Pi OS Lite ARM64 base image."""
    parser = Command().create_parser("manage.py", "imager")

    options = vars(parser.parse_args(["build", "--name", "default-image"]))

    assert options["base_image_uri"] == DEFAULT_RPI4B_BASE_IMAGE_URI


def test_gway_burn_uses_default_base_image_when_not_overridden(monkeypatch):
    """Verify that gway-burn falls back to the default base image when no environment override is set."""
    monkeypatch.delenv("IMAGER_GWAY_BASE_IMAGE_URI", raising=False)

    resolved = Command()._resolve_gway_burn_base_image_uri({"base_image_uri": ""})

    assert resolved == DEFAULT_RPI4B_BASE_IMAGE_URI


def test_gway_burn_environment_override_still_wins(monkeypatch):
    """Verify that IMAGER_GWAY_BASE_IMAGE_URI environment variable takes precedence over the default."""
    monkeypatch.setenv("IMAGER_GWAY_BASE_IMAGE_URI", "file:///tmp/custom.img")

    resolved = Command()._resolve_gway_burn_base_image_uri({"base_image_uri": ""})

    assert resolved == "file:///tmp/custom.img"
