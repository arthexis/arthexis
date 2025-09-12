#!/bin/bash
# reset-screen
# Force Raspberry Pi display output
# Usage: reset-screen [hdmi|tft|rpi]

MODE="${1:-hdmi}"
CONFIG=/boot/config.txt

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must be run as root. Try: sudo $0 [hdmi|tft|rpi]" >&2
  exit 1
fi

case "$MODE" in
  hdmi)
    echo "Resetting display to HDMI..."
    # Remove TFT framebuffer overlays (common for 3.5")
    sed -i '/dtoverlay=ili9341/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35a/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35b/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35c/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35d/d' "$CONFIG"

    # Force HDMI mode
    sed -i '/hdmi_force_hotplug/d' "$CONFIG"
    sed -i '/hdmi_group/d' "$CONFIG"
    sed -i '/hdmi_mode/d' "$CONFIG"

    echo "hdmi_force_hotplug=1" | tee -a "$CONFIG"
    echo "hdmi_group=2" | tee -a "$CONFIG"
    echo "hdmi_mode=82" | tee -a "$CONFIG"
    # mode 82 = 1080p 60Hz

    # Ensure Raspberry Pi display stack
    sed -i '/dtoverlay=vc4-kms-v3d/d' "$CONFIG"
    echo "dtoverlay=vc4-kms-v3d" | tee -a "$CONFIG"
    sed -i '/ignore_lcd/d' "$CONFIG"
    ;;

  tft)
    echo "Resetting display to TFT..."
    # Remove HDMI settings
    sed -i '/hdmi_force_hotplug/d' "$CONFIG"
    sed -i '/hdmi_group/d' "$CONFIG"
    sed -i '/hdmi_mode/d' "$CONFIG"

    # Remove existing TFT overlays to avoid duplicates
    sed -i '/dtoverlay=ili9341/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35a/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35b/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35c/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35d/d' "$CONFIG"

    # Enable a common 3.5" Waveshare overlay
    echo "dtoverlay=waveshare35a" | tee -a "$CONFIG"
    ;;

  rpi)
    echo "Resetting display to Raspberry Pi screen..."
    # Remove HDMI and TFT settings
    sed -i '/hdmi_force_hotplug/d' "$CONFIG"
    sed -i '/hdmi_group/d' "$CONFIG"
    sed -i '/hdmi_mode/d' "$CONFIG"

    sed -i '/dtoverlay=ili9341/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35a/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35b/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35c/d' "$CONFIG"
    sed -i '/dtoverlay=waveshare35d/d' "$CONFIG"

    # Ensure Raspberry Pi screen is enabled
    sed -i '/ignore_lcd/d' "$CONFIG"
    echo "ignore_lcd=0" | tee -a "$CONFIG"
    sed -i '/dtoverlay=vc4-kms-v3d/d' "$CONFIG"
    echo "dtoverlay=vc4-kms-v3d" | tee -a "$CONFIG"
    ;;

  *)
    echo "Usage: $0 [hdmi|tft|rpi]"
    exit 1
    ;;
esac

LOCK_DIR="$(cd "$(dirname "$0")" && pwd)/locks"
mkdir -p "$LOCK_DIR"
echo "$MODE" | tee "$LOCK_DIR/screen_mode.lck" >/dev/null

echo "Done. Rebooting..."
reboot
