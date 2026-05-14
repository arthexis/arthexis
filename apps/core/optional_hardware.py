from __future__ import annotations


def _normalize_detail(detail: object | None) -> str:
    """Return normalized text for optional-hardware log classification."""

    return str(detail or "").strip().lower()


def is_expected_i2c_absence(detail: object | None) -> bool:
    """Return whether ``detail`` describes an expected missing I2C runtime."""

    normalized = _normalize_detail(detail)
    if not normalized:
        return False

    if any(
        marker in normalized
        for marker in (
            "i2cdetect is not available",
            "smbus module not found",
        )
    ):
        return True

    if "no such file or directory" not in normalized:
        return False

    return any(
        marker in normalized
        for marker in (
            "could not open file `/dev/i2c-",
            "i2c bus device for channel",
        )
    )


def is_expected_gpio_absence(detail: object | None) -> bool:
    """Return whether ``detail`` describes an expected missing GPIO/RFID runtime."""

    normalized = _normalize_detail(detail)
    if any(
        marker in normalized
        for marker in (
            "gpio library not available",
            "mfrc522 library not available",
        )
    ):
        return True

    if "no such file or directory" not in normalized:
        return False

    return any(
        marker in normalized
        for marker in (
            "failed to initialize rfid hardware",
            "spidev",
            "/dev/spi",
        )
    )


def is_expected_optional_hardware_absence(detail: object | None) -> bool:
    """Return whether ``detail`` matches optional-hardware absence we should not warn on."""

    return is_expected_gpio_absence(detail) or is_expected_i2c_absence(detail)


__all__ = [
    "is_expected_gpio_absence",
    "is_expected_i2c_absence",
    "is_expected_optional_hardware_absence",
]
