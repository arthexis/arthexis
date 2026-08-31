# LCD screen flicker during boot or upgrade

This guide helps troubleshoot LCD panels that briefly show the start message and then turn off until firmware upgrade or reboot completes.

The LCD service now force-resets the controller whenever it restarts to clear garbled frames that can appear after upgrades.

## Quick checklist
- **Power stability:** Confirm the display has a stable 12–24 V supply; voltage dips can cause the backlight controller to reset.
- **Data cables:** Reseat the LVDS/eDP ribbon and the panel power connector to rule out loose seating.
- **Ambient light sensor:** If the unit has auto-dimming, cover the sensor briefly to ensure the backlight responds instead of shutting off.
- **Firmware state:** The panel may intentionally blank while boot or upgrade tasks run—wait for the process to finish before power cycling.

## Recommended diagnostics
1. **Check logs for backlight cutouts:**
   - Review recent boot logs for display or backlight driver faults.
   - On Linux systems, run `dmesg | grep -i -E "panel|backlight|pwm"` after the flicker occurs.
2. **Verify upgrade progress:**
   - If the flicker happens during an upgrade, watch the controller logs or console for update progress to avoid interrupting firmware flashing.
3. **Test with stable power:**
   - Connect the unit to a bench supply and monitor voltage while the screen blanks.
4. **Force full brightness temporarily:**
   - If supported, set the backlight to 100% in the UI or via a command to rule out aggressive dimming curves.
5. **Inspect thermal conditions:**
   - Over-temperature protection can disable the backlight; check for blocked vents or high ambient temperatures.

## When to escalate
- The screen stays off even after the system finishes booting or upgrading.
- You see repeated backlight or panel driver faults in `dmesg`.
- The issue persists across known-good power sources and cables.

Collect logs, the exact firmware version, and details about connected peripherals before opening a support ticket.
