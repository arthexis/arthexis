"""OCPP 1.6 transaction lifecycle layered on the persistent simulator worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.ocpp.simulator.network import LiveSimulatorError
from apps.ocpp.simulator.worker import LiveSimulatorWorker


@dataclass
class ActiveTransaction:
    transaction_id: int
    connector_id: int
    id_tag: str
    meter_start: int
    started_at: str


class TransactionalLiveSimulatorWorker(LiveSimulatorWorker):
    """Add one-connector transaction state to the generic persistent worker."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self._connector_id = 1
        self._connector_status = "Available"
        self._meter_wh = 0
        self._transaction: ActiveTransaction | None = None

    async def connect_and_boot(self) -> None:
        await super().connect_and_boot()
        async with self._transport_lock:
            await self._send_status("Available")
        self._connector_status = "Available"

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "transaction-start":
            return await self._start_transaction(request)
        if action == "transaction-stop":
            return await self._stop_transaction(request)

        result = await super().dispatch(request)
        if action == "status":
            result.update(self._transaction_status())
        return result

    def _transaction_status(self) -> dict[str, Any]:
        current = self._transaction
        return {
            "connector": self._connector_id,
            "status": self._connector_status,
            "transaction_id": current.transaction_id if current else None,
            "meter_wh": self._meter_wh,
            "started_at": current.started_at if current else None,
        }

    async def _send_status(self, status: str) -> None:
        await self._simulator.call(
            "StatusNotification",
            {
                "connectorId": self._connector_id,
                "errorCode": "NoError",
                "status": status,
                "timestamp": self._clock.isoformat(),
            },
        )

    @staticmethod
    def _meter_value(raw: object, *, name: str) -> int:
        value = int(raw)
        if value < 0:
            raise ValueError(f"{name} must be zero or greater")
        return value

    async def _start_transaction(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._transaction is not None:
            raise LiveSimulatorError(
                f"connector {self._connector_id} already has transaction "
                f"{self._transaction.transaction_id}"
            )
        id_tag = str(request.get("id_tag", "")).strip()
        if not id_tag:
            raise LiveSimulatorError("transaction start requires an idTag")
        connector_id = int(request.get("connector_id", 1))
        if connector_id != 1:
            raise LiveSimulatorError("simulator currently supports connector 1 only")
        meter_start = self._meter_value(
            request.get("meter_start", self._meter_wh), name="meter_start"
        )
        started_at = self._clock.isoformat()

        async with self._transport_lock:
            await self._send_status("Preparing")
            self._connector_status = "Preparing"
            try:
                response = await self._simulator.call(
                    "StartTransaction",
                    {
                        "connectorId": connector_id,
                        "idTag": id_tag,
                        "meterStart": meter_start,
                        "timestamp": started_at,
                    },
                )
            except Exception:
                self._connector_status = "Available"
                try:
                    await self._send_status("Available")
                except Exception:
                    pass
                raise

            transaction_id = response.get("transactionId")
            info = response.get("idTagInfo")
            if not isinstance(info, dict):
                self._connector_status = "Available"
                try:
                    await self._send_status("Available")
                except Exception:
                    pass
                raise LiveSimulatorError(
                    "StartTransaction response requires idTagInfo.status"
                )
            authorization = info.get("status")
            if not isinstance(authorization, str) or not authorization:
                self._connector_status = "Available"
                try:
                    await self._send_status("Available")
                except Exception:
                    pass
                raise LiveSimulatorError(
                    "StartTransaction response requires idTagInfo.status"
                )
            if authorization != "Accepted":
                self._connector_status = "Available"
                await self._send_status("Available")
                return {
                    "ok": True,
                    "charger": self.config.charger,
                    "started": False,
                    "authorization": authorization,
                    **self._transaction_status(),
                }
            if isinstance(transaction_id, bool) or not isinstance(transaction_id, int):
                self._connector_status = "Available"
                try:
                    await self._send_status("Available")
                except Exception:
                    pass
                raise LiveSimulatorError(
                    "StartTransaction response requires integer transactionId"
                )

            self._transaction = ActiveTransaction(
                transaction_id=transaction_id,
                connector_id=connector_id,
                id_tag=id_tag,
                meter_start=meter_start,
                started_at=started_at,
            )
            self._meter_wh = meter_start
            self._connector_status = "Charging"
            await self._send_status("Charging")

        return {
            "ok": True,
            "charger": self.config.charger,
            "started": True,
            "authorization": authorization,
            **self._transaction_status(),
        }

    async def _stop_transaction(self, request: dict[str, Any]) -> dict[str, Any]:
        current = self._transaction
        if current is None:
            raise LiveSimulatorError("connector 1 has no active transaction")
        meter_stop = self._meter_value(
            request.get("meter_stop", self._meter_wh), name="meter_stop"
        )
        if meter_stop < current.meter_start:
            raise LiveSimulatorError("meter_stop cannot be below meter_start")
        stopped_at = self._clock.isoformat()
        payload: dict[str, object] = {
            "meterStop": meter_stop,
            "timestamp": stopped_at,
            "transactionId": current.transaction_id,
            "idTag": current.id_tag,
        }
        reason = request.get("reason")
        if reason:
            payload["reason"] = str(reason)

        async with self._transport_lock:
            await self._send_status("Finishing")
            self._connector_status = "Finishing"
            try:
                response = await self._simulator.call("StopTransaction", payload)
            except Exception:
                self._connector_status = "Charging"
                try:
                    await self._send_status("Charging")
                except Exception:
                    pass
                raise

            info = response.get("idTagInfo")
            authorization = info.get("status") if isinstance(info, dict) else None
            transaction_id = current.transaction_id
            self._meter_wh = meter_stop
            self._transaction = None
            try:
                await self._send_status("Available")
                self._connector_status = "Available"
            except Exception:
                # The CSMS accepted StopTransaction, so never resurrect the local
                # transaction merely because the final status notification failed.
                raise

        return {
            "ok": True,
            "charger": self.config.charger,
            "stopped": True,
            "transaction_id": transaction_id,
            "authorization": authorization,
            "meter_wh": meter_stop,
            "stopped_at": stopped_at,
            "connector": self._connector_id,
            "status": self._connector_status,
        }
