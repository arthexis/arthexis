"""Exceptions translated into OCPP CallError frames."""


class ProtocolFrameError(ValueError):
    """A malformed OCPP frame with a safe protocol-facing explanation."""

    def __init__(self, code: str, description: str) -> None:
        super().__init__(description)
        self.code = code
        self.description = description


class OutboundCallError(RuntimeError):
    """A charge point returned an OCPP CallError for an outbound request."""

    def __init__(self, code: str, description: str) -> None:
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


class OutboundCallTimeout(TimeoutError):
    """A charge point did not complete an outbound OCPP request in time."""


class ConnectionClosed(RuntimeError):
    """An OCPP connection closed before a pending request completed."""
