import os
import sqlite3
import time
from datetime import datetime

def to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return None


def main() -> None:
    db_path = os.environ.get("ARTHEXIS_STOP_DB_PATH")
    if not db_path:
        db_path = os.environ.get("ARTHEXIS_SQLITE_PATH", "")
    if not db_path:
        db_path = os.path.join(os.getcwd(), "db.sqlite3")
    stale_after = int(os.environ.get("CHARGING_SESSION_STALE_AFTER_SECONDS", "86400"))
    now = time.time()
    active = 0
    stale = 0

    if db_path and os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT start_time, received_start_time, stop_time, connector_id FROM ocpp_transaction"
        )
        for start_time, received_start_time, stop_time, connector_id in cur.fetchall():
            if connector_id is None or stop_time is not None:
                continue
            active += 1
            ts = to_epoch(received_start_time) or to_epoch(start_time)
            if ts is not None and now - ts > stale_after:
                stale += 1
        conn.close()

    print(f"{active} {stale}")


if __name__ == "__main__":
    main()
