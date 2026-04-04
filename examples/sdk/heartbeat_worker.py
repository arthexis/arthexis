"""Background worker example that emits heartbeats and events via arthexis.sdk."""

from __future__ import annotations

import signal
import time

from arthexis.sdk import (
    ArthexisClient,
    ArthexisClientConfig,
    DeviceHeartbeatRequest,
    EventSubmissionRequest,
    RetryPolicy,
)

RUNNING = True


def _stop(_signal_number: int, _frame: object) -> None:
    global RUNNING
    RUNNING = False


def main() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    client = ArthexisClient(
        ArthexisClientConfig(
            base_url="https://arthexis.example.com",
            api_key="replace-me",
            retry_policy=RetryPolicy(max_attempts=4, base_delay_seconds=0.5),
        )
    )

    while RUNNING:
        heartbeat = client.device_heartbeat(
            DeviceHeartbeatRequest(
                device_id="SIM-CP-1",
                firmware_version="1.2.3",
                status="online",
                metrics={"temperature_c": 31.2},
            )
        )

        if heartbeat.accepted:
            client.submit_event_http(
                EventSubmissionRequest(
                    device_id="SIM-CP-1",
                    event_type="worker.heartbeat.accepted",
                    payload={"server_time": heartbeat.server_time},
                )
            )

        time.sleep(max(heartbeat.next_heartbeat_seconds, 1))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
