from __future__ import annotations

from apps.core.optional_hardware import is_expected_gpio_absence, is_expected_i2c_absence


def test_is_expected_i2c_absence_matches_simple_markers() -> None:
    assert is_expected_i2c_absence("i2cdetect is not available") is True
    assert is_expected_i2c_absence("smbus module not found") is True


def test_is_expected_i2c_absence_matches_missing_device_patterns() -> None:
    assert is_expected_i2c_absence(
        "Could not open file `/dev/i2c-1`: No such file or directory"
    )
    assert is_expected_i2c_absence(
        "I2C bus device for channel 1: No such file or directory"
    )


def test_is_expected_i2c_absence_rejects_unrelated_messages() -> None:
    assert is_expected_i2c_absence(
        "I2C bus device for channel 1: permission denied"
    ) is False
    assert is_expected_i2c_absence("other runtime error") is False


def test_is_expected_gpio_absence_matches_missing_device_patterns() -> None:
    assert is_expected_gpio_absence(
        "Failed to initialize RFID hardware: [Errno 2] No such file or directory: '/dev/spidev0.0'"
    )


def test_is_expected_gpio_absence_rejects_unrelated_messages() -> None:
    assert is_expected_gpio_absence(
        "Failed to initialize RFID hardware: permission denied"
    ) is False
