"""CLI example for starting and stopping sessions with arthexis.sdk."""

from __future__ import annotations

import argparse

from arthexis.sdk import (
    ArthexisClient,
    ArthexisClientConfig,
    StartSessionRequest,
    StopSessionRequest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Session operations via Arthexis SDK")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--connector-id", required=True)
    parser.add_argument("--id-tag", required=True)
    parser.add_argument("--stop-reason", default="Local")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    client = ArthexisClient(
        ArthexisClientConfig(base_url=args.base_url, api_key=args.api_key)
    )
    started = client.start_session(
        StartSessionRequest(
            device_id=args.device_id,
            connector_id=args.connector_id,
            id_tag=args.id_tag,
        )
    )
    print(f"started={started.accepted} session={started.session_id} status={started.status}")

    stopped = client.stop_session(
        StopSessionRequest(session_id=started.session_id, reason=args.stop_reason)
    )
    print(f"stopped={stopped.accepted} session={stopped.session_id} status={stopped.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
