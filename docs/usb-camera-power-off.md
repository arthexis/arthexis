# Powering off a USB camera (Ubuntu)

This guide outlines a safe, Ubuntu-only approach for **physically powering off** a USB camera by
switching power at the USB port. The most reliable method is to use a hub that supports per-port
power control and the `uhubctl` utility.

## Recommended: Use a power-switching USB hub (uhubctl)

`uhubctl` talks to compatible USB hubs and root hubs that support port power switching.
When supported, it **cuts power** to the specific USB port.

### 1) Install uhubctl

```bash
sudo apt update
sudo apt install uhubctl
```

### 2) Find the camera's USB path

```bash
lsusb -t
```

Look for the camera in the tree to identify the **bus** and **port**.
You can also inspect device details:

```bash
lsusb
lsusb -v -d <vid:pid>
```

### 3) Power off the camera's port

Replace `<bus>` and `<port>` with the values from the USB tree.

```bash
sudo uhubctl -l <bus> -p <port> -a off
```

To restore power:

```bash
sudo uhubctl -l <bus> -p <port> -a on
```

### Notes

- **Not all hubs support per-port power switching.** `uhubctl` will report capabilities.
- If your built-in root hub does not support power switching, use an **external powered hub**
  that does.

## Fallback: Unbind the USB device (may not cut power)

If you cannot switch power, you can unbind the USB device from the kernel driver. This often
**disables** the camera but may not cut power to the port.

### 1) Find the device path

```bash
ls /sys/bus/usb/devices
```

Identify the device name (e.g., `1-2`, `3-1.4`) by matching it to `lsusb -t`.

### 2) Unbind and rebind

```bash
echo '<device>' | sudo tee /sys/bus/usb/drivers/usb/unbind
```

To rebind:

```bash
echo '<device>' | sudo tee /sys/bus/usb/drivers/usb/bind
```

## Summary

- **Best option:** `uhubctl` with a power-switching USB hub to physically cut power.
- **Fallback:** unbind the USB device (disables it but may not remove power).
