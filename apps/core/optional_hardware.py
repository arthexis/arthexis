from __future__ import annotations


def _normalize_detail(detail: object | None) -> str:
    """Return normalized text for optional-hardware log classification."""

    return str(detail or "").strip().lower()


def is_expected_i2c_absence(detail: object | None) -> bool:
    """Return whether ``detail`` describes an expected missing I2C runtime."""

    normalized = _normalize_detail(detail)
    if not normalized:
        return False

    if "i2cdetect is not available" in normalized:
        return True

    if "smbus module not found" in normalized:
        return True

    if (
        "could not open file `/dev/i2c-" in normalized
        and "no such file or directory" in normalized
    ):
        return True

    if (
        "i2c bus device for channel" in normalized
        and "no such file or directory" in normalized
    ):
        return True

    return False


def is_expected_gpio_absence(detail: object | None) -> bool:
    """Return whether ``detail`` describes an expected missing GPIO/RFID runtime."""

    normalized = _normalize_detail(detail)
    return any(
        marker in normalized
        for marker in (
            "gpio library not available",
            "mfrc522 library not available",
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
