import base64


def basic_authorization(
    identity: str = "charger-1",
    token: str = "charger-secret",
) -> bytes:
    encoded = base64.b64encode(f"{identity}:{token}".encode())
    return b"Basic " + encoded


from channels.testing import WebsocketCommunicator

from arthexis.asgi import application


async def connect_charger(
    *,
    identity: str = "charger-1",
    token: str = "charger-secret",
    subprotocol: str = "ocpp1.6",
) -> WebsocketCommunicator:
    """Open one authenticated OCPP test connection and verify negotiation."""
    communicator = WebsocketCommunicator(
        application,
        f"/ws/ocpp/{identity}/",
        subprotocols=[subprotocol],
        headers=[(b"authorization", basic_authorization(identity, token))],
    )
    connected, negotiated = await communicator.connect(timeout=5)
    if not connected:
        raise AssertionError(f"Could not connect OCPP test charger {identity}.")
    if negotiated != subprotocol:
        raise AssertionError(
            f"Expected subprotocol {subprotocol!r}, got {negotiated!r}."
        )
    return communicator
