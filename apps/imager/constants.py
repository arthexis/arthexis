"""Shared constants for image artifact workflows."""

DEFAULT_ARTHEXIS_GIT_URL = ""
DEFAULT_RPI4B_BASE_IMAGE_URI = "https://downloads.raspberrypi.com/raspios_lite_arm64_latest"
DEFAULT_RPI4B_BASE_IMAGE_DESCRIPTION = "Raspberry Pi OS Lite (Debian, arm64)"
UNIVERSAL_CONNECT_UPDATE_ROLES = ("Terminal", "Satellite", "Control", "Watchtower")
UNIVERSAL_CONNECT_UPDATE_REQUIRED_ARTIFACTS = (
    "connect-ota-agent",
    "connect-ota-channel-config",
    "connect-ota-device-identity",
)
