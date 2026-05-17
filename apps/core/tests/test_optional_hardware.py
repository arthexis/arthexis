from __future__ import annotations

from apps.core.optional_hardware import (
    is_expected_gpio_absence,
    is_expected_i2c_absence,
)


def test_expected_i2c_absence_classification() -> None:
    expected_absence_messages = [
        "i2cdetect is not available",
        "smbus module not found",
        "Could not open file `/dev/i2c-1`: No such file or directory",
        "I2C bus device for channel 1: No such file or directory",
    ]
    unrelated_messages = [
        "I2C bus device for channel 1: permission denied",
        "other runtime error",
    ]

    for message in expected_absence_messages:
        assert is_expected_i2c_absence(message) is True
    for message in unrelated_messages:
        assert is_expected_i2c_absence(message) is False


def test_expected_gpio_absence_classification() -> None:
    missing_device_message = (
        "Failed to initialize RFID hardware: [Errno 2] "
        "No such file or directory: '/dev/spidev0.0'"
    )

    assert (
        is_expected_gpio_absence(missing_device_message)
        is True
    )
    assert (
        is_expected_gpio_absence(
            "Failed to initialize RFID hardware: permission denied"
        )
        is False
    )
