"""GPIO pin helpers using the gpiozero library."""

try:  # pragma: no cover - library optional
    from gpiozero import LED, Device
    from gpiozero.pins.mock import MockFactory
except Exception:  # pragma: no cover - missing on non-Pi systems
    LED = None
    Device = None
    MockFactory = None


class LEDController:
    """Control an LED attached to a GPIO pin.

    The controller automatically configures gpiozero to use the
    :class:`~gpiozero.pins.mock.MockFactory` when no pin factory has been
    set, allowing code to run on systems without GPIO hardware.
    """

    def __init__(self, pin: int):
        if LED is None or Device is None or MockFactory is None:
            raise RuntimeError("gpiozero library not available")
        if Device.pin_factory is None:  # pragma: no cover - branch executed
            Device.pin_factory = MockFactory()
        self._led = LED(pin)

    def on(self) -> None:
        """Turn the LED on."""
        self._led.on()

    def off(self) -> None:
        """Turn the LED off."""
        self._led.off()

    @property
    def is_lit(self) -> bool:
        """Return ``True`` if the LED is currently on."""
        return self._led.is_lit

    def close(self) -> None:
        """Release the underlying gpiozero resources."""
        self._led.close()


__all__ = ["LEDController"]
