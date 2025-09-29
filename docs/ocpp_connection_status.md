# OCPP Connection Status Diagnostics

This note summarizes how the CSMS signals successful charger connections and how to interpret captures when nothing reaches the application logs.

## What the CSMS records

* When a charger handshake succeeds, the CSMS accepts the socket and records a log entry similar to `Connected (subprotocol=...)`. The subprotocol is only negotiated when the client offers `ocpp1.6`; otherwise the connection is still accepted and logged with `subprotocol=none`.
* The serial number is taken from the WebSocket path or the common OCPP query parameters (`cid`, `chargePointId`, `charge_point_id`, `chargeBoxId`, `charge_box_id`, `chargerId`). Parameter names are matched case-insensitively and whitespace-only values are ignored.

If the most recent charger log is weeks old, the EVCS is not completing the WebSocket handshake today. No new attempt has made it past the TCP/TLS negotiation or the HTTP upgrade request.

## Reading the packet capture

In the provided Wireshark capture the trace stops at repeated TLS `Client Hello` retransmissions with no corresponding `Server Hello`. That means the EVCS opened a TCP session to the gateway, attempted to begin TLS, and never received a TLS response. Because the TLS negotiation never completes, the CSMS code above is never invoked and no WebSocket upgrade or OCPP frame is exchanged.

This behaviour usually indicates one of the following:

1. **The gateway is listening on a different port/protocol.** Double-check that the EVCS is pointed at the HTTPS listener (typically 443 or 8443) and that any reverse proxy forwards TLS to Django.
2. **A middlebox is dropping or terminating TLS.** Firewalls, TLS-terminating proxies, or captive portals can accept the TCP SYN but block or rewrite the TLS handshake.
3. **Certificate or protocol mismatch.** If the EVCS enforces TLS versions or cipher suites that the gateway cannot satisfy, the handshake may be aborted before the application sees it.

## Next steps

* Confirm the EVCS target URL matches the gateway host and TLS port and that it uses `wss://`.
* If a reverse proxy is in play, capture on the proxy as well to ensure the TLS `Client Hello` reaches Django.
* Test with protocol version enforcement disabled on the EVCS if possible; the CSMS already accepts connections without an `ocpp1.6` subprotocol.
* Once the TLS handshake succeeds, check `logs/charger.<SERIAL>.log` for a fresh `Connected` message to verify the CSMS registered the session.
