#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def encode_jwt(*, secret: str, role: str, ttl: int, satellite_id: str | None = None) -> str:
    if len(secret) < 32:
        raise ValueError("JWT secret must be at least 32 characters")
    if ttl < 1:
        raise ValueError("TTL must be positive")
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload: dict[str, object] = {
        "role": role,
        "iat": now,
        "exp": now + ttl,
    }
    if satellite_id is not None:
        payload["satellite_id"] = satellite_id
    encoded_header = _b64(json.dumps(header, sort_keys=True, separators=(",", ":")).encode())
    encoded_payload = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = _b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{encoded_header}.{encoded_payload}.{signature}"


def main() -> int:
    parser = argparse.ArgumentParser(prog="ocpp-collector-token")
    parser.add_argument(
        "--secret",
        default=os.environ.get("OCPP_COLLECTOR_JWT_SECRET", ""),
        help="JWT signing secret; defaults to OCPP_COLLECTOR_JWT_SECRET",
    )
    parser.add_argument("--ttl", type=int, default=7_776_000, help="Token lifetime in seconds")
    sub = parser.add_subparsers(dest="kind", required=True)

    forwarder = sub.add_parser("forwarder", help="Issue an OCPP Forwarder token")
    forwarder.add_argument("satellite_id")

    sub.add_parser("reader", help="Issue an Arthexis read-only token")

    args = parser.parse_args()
    if args.kind == "forwarder":
        token = encode_jwt(
            secret=args.secret,
            role="ocpp_forwarder",
            ttl=args.ttl,
            satellite_id=args.satellite_id,
        )
    else:
        token = encode_jwt(
            secret=args.secret,
            role="arthexis_reader",
            ttl=args.ttl,
        )
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
