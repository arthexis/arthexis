#!/bin/bash
# reset-hdmi.sh
# Force Raspberry Pi to output on HDMI again

echo "Resetting display to HDMI..."

# Remove TFT framebuffer overlays (common for 3.5")
sudo sed -i '/dtoverlay=ili9341/d' /boot/config.txt
sudo sed -i '/dtoverlay=waveshare35a/d' /boot/config.txt
sudo sed -i '/dtoverlay=waveshare35b/d' /boot/config.txt
sudo sed -i '/dtoverlay=waveshare35c/d' /boot/config.txt
sudo sed -i '/dtoverlay=waveshare35d/d' /boot/config.txt

# Force HDMI mode
sudo sed -i '/hdmi_force_hotplug/d' /boot/config.txt
sudo sed -i '/hdmi_group/d' /boot/config.txt
sudo sed -i '/hdmi_mode/d' /boot/config.txt

echo "hdmi_force_hotplug=1" | sudo tee -a /boot/config.txt
echo "hdmi_group=2" | sudo tee -a /boot/config.txt
echo "hdmi_mode=82" | sudo tee -a /boot/config.txt
# mode 82 = 1080p 60Hz

LOCK_DIR="$(cd "$(dirname "$0")" && pwd)/locks"
sudo mkdir -p "$LOCK_DIR"
echo "hdmi" | sudo tee "$LOCK_DIR/screen_mode.lck" >/dev/null

echo "Done. Rebooting..."
sudo reboot
