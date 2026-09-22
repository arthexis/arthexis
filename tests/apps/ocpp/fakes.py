"""Protocol-agnostic test doubles shared by OCPP suites."""


class RecordingSender:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send(
        self,
        *,
        action: str,
        payload: dict[str, object],
        timeout: float = 30,
        unique_id: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "action": action,
                "payload": payload,
                "timeout": timeout,
                "unique_id": unique_id,
            }
        )
        return {"status": "Accepted"}
