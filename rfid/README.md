# RFID Reader Interface

This app provides a simple interface for reading RFID tags using the **RC522** module on a Raspberry Pi.

## Components Required

- Raspberry Pi with GPIO (e.g. Pi 3/4)
- RC522 RFID reader module
- RFID tags or cards
- Jumper wires

## Wiring RC522 to Raspberry Pi

| RC522 Pin | Raspberry Pi Pin |
| --------- | ---------------- |
| SDA       | GPIO 24 (Pin 18) |
| SCK       | GPIO 11 (Pin 23) |
| MOSI      | GPIO 10 (Pin 19) |
| MISO      | GPIO 9 (Pin 21)  |
| IRQ       | Not connected    |
| GND       | GND (Pin 6)      |
| RST       | GPIO 25 (Pin 22) |
| 3.3V      | 3.3V (Pin 1)     |

## Enable SPI

1. Run `sudo raspi-config`.
2. Go to **Interfacing Options** → **SPI** and enable it.
3. Reboot with `sudo reboot`.

## Install Dependencies

```bash
sudo apt update
sudo apt install python3-pip python3-dev libspi-dev
pip3 install mfrc522
```

## Reading Tags

The management command `read_rfid` will read a tag and print its ID and text:

```bash
python manage.py read_rfid
```

Example output:

```
Place your RFID tag near the reader...
ID: 123456789
Text: hello
```

For direct use, the `RFID` app exposes the `RC522Reader` class in `rfid.reader`:

```python
from rfid.reader import RC522Reader

reader = RC522Reader()
card_id, text = reader.read()
print(card_id, text)
```
