"""OCPP 1.6 transaction lifecycle layered on the persistent simulator worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from apps.ocpp.simulator.network import LiveSimulatorError
from apps.ocpp.simulator.worker import LiveSimulatorWorker


@dataclass
class ActiveTransaction:
    transaction_id: int
    connector_id: int
    id_tag: str | None
    meter_start: int
    started_at: str


class TransactionalLiveSimulatorWorker(LiveSimulatorWorker):
    """Add one-connector transaction and deterministic meter state."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self._connector_id = 1
        self._connector_status = "Available"
        self._meter_wh_exact = 0.0
        self._power_w = 0.0
        self._current_a: float | None = None
        self._voltage_v: float | None = None
        self._meter_anchor: datetime = self._clock.now()
        self._transaction: ActiveTransaction | None = None
        self._recover_transaction_state()

    @property
    def _meter_wh(self) -> int:
        return int(self._meter_wh_exact)

    def _recover_transaction_state(self) -> None:
        state = self._requests.latest_event("TransactionState")
        if not state:
            return
        self._meter_wh_exact = float(state.get("meter_wh", 0))
        self._power_w = float(state.get("power_w", 0.0))
        current_a = state.get("current_a")
        self._current_a = float(current_a) if current_a is not None else None
        voltage_v = state.get("voltage_v")
        self._voltage_v = float(voltage_v) if voltage_v is not None else None
        self._connector_status = str(state.get("status", "Available"))
        transaction_id = state.get("transaction_id")
        if transaction_id is None:
            self._transaction = None
            self._connector_status = "Available"
            return
        self._transaction = ActiveTransaction(
            transaction_id=int(transaction_id),
            connector_id=int(state.get("connector", 1)),
            id_tag=None,
            meter_start=int(state.get("meter_start", self._meter_wh)),
            started_at=str(state.get("started_at", "")),
        )
        self._connector_status = "Charging"

    def _persist_transaction_state(self) -> None:
        current = self._transaction
        self._requests.event(
            action="TransactionState",
            state="active" if current else "idle",
            charger=self.config.charger,
            charger_time=self._clock.isoformat(),
            connector=self._connector_id,
            status=self._connector_status,
            transaction_id=current.transaction_id if current else None,
            meter_start=current.meter_start if current else None,
            meter_wh=self._meter_wh,
            power_w=self._power_w,
            current_a=self._current_a,
            voltage_v=self._voltage_v,
            started_at=current.started_at if current else None,
        )

    def _begin_request(self, action: str, **fields: Any) -> str:
        request_id = self._requests.new_request_id()
        self._requests.event(
            request_id=request_id,
            action=action,
            state="submitted",
            charger=self.config.charger,
            charger_time=self._clock.isoformat(),
            **fields,
        )
        return request_id

    def _complete_request(self, request_id: str, action: str, **fields: Any) -> None:
        self._requests.complete(
            request_id,
            action=action,
            charger=self.config.charger,
            charger_time=self._clock.isoformat(),
            **fields,
        )

    async def connect_and_boot(self) -> None:
        await super().connect_and_boot()
        announced = "Charging" if self._transaction is not None else "Available"
        async with self._transport_lock:
            await self._send_status(announced)
        self._connector_status = announced
        self._meter_anchor = self._clock.now()
        self._persist_transaction_state()

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "transaction-start":
            return await self._start_transaction(request)
        if action == "transaction-stop":
            return await self._stop_transaction(request)
        if action == "meter":
            return await self._meter(request)

        result = await super().dispatch(request)
        if action == "status":
            self._accumulate_meter()
            result.update(self._transaction_status())
        return result

    def _transaction_status(self) -> dict[str, Any]:
        current = self._transaction
        return {
            "connector": self._connector_id,
            "status": self._connector_status,
            "transaction_id": current.transaction_id if current else None,
            "meter_wh": self._meter_wh,
            "power_w": self._power_w,
            "current_a": self._current_a,
            "voltage_v": self._voltage_v,
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
    def _nonnegative(raw: object, *, name: str) -> float:
        value = float(raw)
        if value < 0:
            raise ValueError(f"{name} must be zero or greater")
        return value

    @classmethod
    def _meter_value(cls, raw: object, *, name: str) -> int:
        return int(cls._nonnegative(raw, name=name))

    def _accumulate_meter(self) -> None:
        now = self._clock.now()
        elapsed_seconds = max(0.0, (now - self._meter_anchor).total_seconds())
        if self._transaction is not None and self._power_w > 0 and elapsed_seconds > 0:
            self._meter_wh_exact += self._power_w * elapsed_seconds / 3600.0
        self._meter_anchor = now

    def _set_meter_energy(self, energy_wh: object) -> None:
        value = self._nonnegative(energy_wh, name="energy_wh")
        if self._transaction is not None and value < self._transaction.meter_start:
            raise LiveSimulatorError("energy_wh cannot be below meter_start")
        if value < self._meter_wh_exact:
            raise LiveSimulatorError("energy_wh cannot decrease the cumulative meter")
        self._meter_wh_exact = value

    def _set_electrical_state(self, request: dict[str, Any]) -> None:
        if request.get("power_w") is not None:
            self._power_w = self._nonnegative(request["power_w"], name="power_w")
        if request.get("current_a") is not None:
            self._current_a = self._nonnegative(request["current_a"], name="current_a")
        if request.get("voltage_v") is not None:
            self._voltage_v = self._nonnegative(request["voltage_v"], name="voltage_v")

    def _meter_values_payload(self, timestamp: str) -> dict[str, object]:
        current = self._transaction
        if current is None:
            raise LiveSimulatorError("connector 1 has no active transaction")
        sampled: list[dict[str, str]] = [
            {
                "value": str(self._meter_wh),
                "measurand": "Energy.Active.Import.Register",
                "unit": "Wh",
            },
            {
                "value": str(self._power_w),
                "measurand": "Power.Active.Import",
                "unit": "W",
            },
        ]
        if self._current_a is not None:
            sampled.append(
                {"value": str(self._current_a), "measurand": "Current.Import", "unit": "A"}
            )
        if self._voltage_v is not None:
            sampled.append(
                {"value": str(self._voltage_v), "measurand": "Voltage", "unit": "V"}
            )
        return {
            "connectorId": self._connector_id,
            "transactionId": current.transaction_id,
            "meterValue": [{"timestamp": timestamp, "sampledValue": sampled}],
        }

    async def _meter(self, request: dict[str, Any]) -> dict[str, Any]:
        current = self._transaction
        if current is None:
            raise LiveSimulatorError("connector 1 has no active transaction")
        request_id = self._begin_request(
            "MeterValues",
            transaction_id=current.transaction_id,
            connector=self._connector_id,
        )
        try:
            self._accumulate_meter()
            if request.get("energy_wh") is not None:
                self._set_meter_energy(request["energy_wh"])
            self._set_electrical_state(request)
            timestamp = self._clock.isoformat()
            payload = self._meter_values_payload(timestamp)
            async with self._transport_lock:
                await self._simulator.call("MeterValues", payload)
        except Exception as exc:
            self._complete_request(request_id, "MeterValues", error=str(exc))
            self._persist_transaction_state()
            raise
        self._persist_transaction_state()
        self._complete_request(
            request_id,
            "MeterValues",
            error=None,
            transaction_id=current.transaction_id,
            meter_wh=self._meter_wh,
        )
        return {
            "ok": True,
            "charger": self.config.charger,
            "request_id": request_id,
            "metered": True,
            "timestamp": timestamp,
            **self._transaction_status(),
        }

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
        request_id = self._begin_request(
            "StartTransaction",
            connector=connector_id,
            id_tag_sha256=self._requests.id_tag_fingerprint(id_tag),
        )
        try:
            self._accumulate_meter()
            meter_start = self._meter_value(
                request.get("meter_start", self._meter_wh), name="meter_start"
            )
            if meter_start < self._meter_wh:
                raise LiveSimulatorError("meter_start cannot decrease the cumulative meter")
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
                    raise LiveSimulatorError("StartTransaction response requires idTagInfo.status")
                authorization = info.get("status")
                if not isinstance(authorization, str) or not authorization:
                    self._connector_status = "Available"
                    try:
                        await self._send_status("Available")
                    except Exception:
                        pass
                    raise LiveSimulatorError("StartTransaction response requires idTagInfo.status")
                if authorization != "Accepted":
                    self._connector_status = "Available"
                    await self._send_status("Available")
                    self._persist_transaction_state()
                    self._complete_request(
                        request_id,
                        "StartTransaction",
                        error=None,
                        authorization=authorization,
                        started=False,
                    )
                    return {
                        "ok": True,
                        "charger": self.config.charger,
                        "request_id": request_id,
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
                    raise LiveSimulatorError("StartTransaction response requires integer transactionId")

                self._transaction = ActiveTransaction(
                    transaction_id=transaction_id,
                    connector_id=connector_id,
                    id_tag=id_tag,
                    meter_start=meter_start,
                    started_at=started_at,
                )
                self._meter_wh_exact = float(meter_start)
                self._meter_anchor = self._clock.now()
                self._connector_status = "Charging"
                await self._send_status("Charging")
        except Exception as exc:
            self._complete_request(request_id, "StartTransaction", error=str(exc))
            self._persist_transaction_state()
            raise

        self._persist_transaction_state()
        self._complete_request(
            request_id,
            "StartTransaction",
            error=None,
            authorization=authorization,
            started=True,
            transaction_id=transaction_id,
            meter_wh=self._meter_wh,
        )
        return {
            "ok": True,
            "charger": self.config.charger,
            "request_id": request_id,
            "started": True,
            "authorization": authorization,
            **self._transaction_status(),
        }

    async def _stop_transaction(self, request: dict[str, Any]) -> dict[str, Any]:
        current = self._transaction
        if current is None:
            raise LiveSimulatorError("connector 1 has no active transaction")
        request_id = self._begin_request(
            "StopTransaction",
            transaction_id=current.transaction_id,
            connector=self._connector_id,
        )
        self._accumulate_meter()
        accumulated_meter = self._meter_wh_exact
        try:
            if request.get("meter_stop") is not None:
                meter_stop = self._meter_value(request["meter_stop"], name="meter_stop")
                if meter_stop < self._meter_wh:
                    raise LiveSimulatorError("meter_stop cannot decrease the cumulative meter")
                self._meter_wh_exact = float(meter_stop)
            meter_stop = self._meter_wh
            if meter_stop < current.meter_start:
                raise LiveSimulatorError("meter_stop cannot be below meter_start")
            stopped_at = self._clock.isoformat()
            payload: dict[str, object] = {
                "meterStop": meter_stop,
                "timestamp": stopped_at,
                "transactionId": current.transaction_id,
            }
            if current.id_tag:
                payload["idTag"] = current.id_tag
            reason = request.get("reason")
            if reason:
                payload["reason"] = str(reason)

            async with self._transport_lock:
                await self._send_status("Finishing")
                self._connector_status = "Finishing"
                try:
                    response = await self._simulator.call("StopTransaction", payload)
                except Exception:
                    self._meter_wh_exact = accumulated_meter
                    self._connector_status = "Charging"
                    try:
                        await self._send_status("Charging")
                    except Exception:
                        pass
                    raise

                info = response.get("idTagInfo")
                authorization = info.get("status") if isinstance(info, dict) else None
                transaction_id = current.transaction_id
                self._transaction = None
                self._power_w = 0.0
                self._meter_anchor = self._clock.now()
                await self._send_status("Available")
                self._connector_status = "Available"
        except Exception as exc:
            self._complete_request(request_id, "StopTransaction", error=str(exc))
            self._persist_transaction_state()
            raise

        self._persist_transaction_state()
        self._complete_request(
            request_id,
            "StopTransaction",
            error=None,
            transaction_id=transaction_id,
            meter_wh=meter_stop,
        )
        return {
            "ok": True,
            "charger": self.config.charger,
            "request_id": request_id,
            "stopped": True,
            "transaction_id": transaction_id,
            "authorization": authorization,
            "meter_wh": meter_stop,
            "stopped_at": stopped_at,
            "connector": self._connector_id,
            "status": self._connector_status,
        }
