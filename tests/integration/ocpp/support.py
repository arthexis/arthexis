import base64


def basic_authorization(
    identity: str = "charger-1",
    token: str = "charger-secret",
) -> bytes:
    encoded = base64.b64encode(f"{identity}:{token}".encode())
    return b"Basic " + encoded
