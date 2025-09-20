"""Shared configuration for the RFID hardware integration."""

from __future__ import annotations

from collections import OrderedDict


# Mapping between the RC522 header labels and the Raspberry Pi connections.
# The wiring strings mirror the labeling used in the installation manual to
# make it easier to cross-reference hardware documentation with the code.
MODULE_WIRING: "OrderedDict[str, str]" = OrderedDict(
    [
        ("SDA", "CE0"),
        ("SCK", "SCLK"),
        ("MOSI", "MOSI"),
        ("MISO", "MISO"),
        ("IRQ", "IO4"),
        ("GND", "GND"),
        ("RST", "IO25"),
        ("3v3", "3v3"),
    ]
)


# SPI configuration: bus 0 / device 0 corresponds to CE0 which matches the
# ``SDA`` wiring entry above.
SPI_BUS = 0
SPI_DEVICE = 0


# RPi.GPIO constants are not available in test environments, but their numeric
# values are stable (GPIO.BCM == 11).  Using BCM numbering matches the wiring
# table which references pins as ``IO`` identifiers.
GPIO_PIN_MODE_BCM = 11


# Derived GPIO pins expressed using BCM numbering.
DEFAULT_IRQ_PIN = 4
DEFAULT_RST_PIN = 25


__all__ = [
    "MODULE_WIRING",
    "SPI_BUS",
    "SPI_DEVICE",
    "GPIO_PIN_MODE_BCM",
    "DEFAULT_IRQ_PIN",
    "DEFAULT_RST_PIN",
]
