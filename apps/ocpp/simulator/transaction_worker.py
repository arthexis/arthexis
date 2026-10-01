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


@dataclass
class ConnectorState:
    connector_id: int
    status: str = "Available"
    meter_wh_exact: float = 0.0
    power_w: float = 0.0
    current_a: float | None = None
    voltage_v: float | None = None
    meter_anchor: datetime | None = None
    transaction: ActiveTransaction | None = None

    @property
    def meter_wh(self) -> int:
        return int(self.meter_wh_exact)


class TransactionalLiveSimulatorWorker(LiveSimulatorWorker):
    """Add independent transactions for every configured physical connector."""

    def __post_init__(self) -> None:
        super().__post_init__()
        now = self._clock.now()
        self._connectors = {
            connector_id: ConnectorState(connector_id, meter_anchor=now)
            for connector_id in range(1, self.config.connectors + 1)
        }
        self._recover_transaction_state()

    # Compatibility for older one-connector tests and integrations.
    @property
    def _meter_anchor(self) -> datetime:
        return self._connectors[1].meter_anchor or self._clock.now()

    @_meter_anchor.setter
    def _meter_anchor(self, value: datetime) -> None:
        self._connectors[1].meter_anchor = value

    @property
    def _meter_wh(self) -> int:
        return self._connectors[1].meter_wh

    def _recover_transaction_state(self) -> None:
        states = self._requests.latest_events("TransactionState", key="connector")
        for raw_connector, payload in states.items():
            try:
                connector_id = int(raw_connector)
            except (TypeError, ValueError):
                continue
            state = self._connectors.get(connector_id)
            if state is None:
                continue
            state.meter_wh_exact = float(
                payload.get("meter_wh_exact", payload.get("meter_wh", 0))
            )
            state.power_w = float(payload.get("power_w", 0.0))
            current_a = payload.get("current_a")
            state.current_a = float(current_a) if current_a is not None else None
            voltage_v = payload.get("voltage_v")
            state.voltage_v = float(voltage_v) if voltage_v is not None else None
            state.status = str(payload.get("status", "Available"))
            transaction_id = payload.get("transaction_id")
            if transaction_id is None:
                state.transaction = None
                state.status = "Available"
                continue
            state.transaction = ActiveTransaction(
                transaction_id=int(transaction_id),
                connector_id=connector_id,
                id_tag=None,
                meter_start=int(payload.get("meter_start", state.meter_wh)),
                started_at=str(payload.get("started_at", "")),
            )
            state.status = "Charging"

    def _persist_transaction_state(self, state: ConnectorState) -> None:
        current = state.transaction
        self._requests.event(
            action="TransactionState",
            state="active" if current else "idle",
            charger=self.config.charger,
            charger_time=self._clock.isoformat(),
            connector=state.connector_id,
            status=state.status,
            transaction_id=current.transaction_id if current else None,
            meter_start=current.meter_start if current else None,
            meter_wh=state.meter_wh,
            meter_wh_exact=state.meter_wh_exact,
            power_w=state.power_w,
            current_a=state.current_a,
            voltage_v=state.voltage_v,
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
        async with self._transport_lock:
            for state in self._connectors.values():
                announced = "Charging" if state.transaction is not None else "Available"
                await self._send_status(state, announced)
                state.status = announced
                state.meter_anchor = self._clock.now()
                self._persist_transaction_state(state)

    async def reconnect(self) -> None:
        await super().reconnect()
        async with self._transport_lock:
            for state in self._connectors.values():
                announced = "Charging" if state.transaction is not None else "Available"
                await self._send_status(state, announced)
                state.status = announced
                state.meter_anchor = self._clock.now()
                self._persist_transaction_state(state)

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
            for state in self._connectors.values():
                self._accumulate_meter(state)
            connector_results = [
                self._transaction_status(state) for state in self._connectors.values()
            ]
            result["connector_count"] = self.config.connectors
            result["connectors"] = connector_results
            if self.config.connectors == 1:
                result.update(connector_results[0])
        return result

    def _state_for_explicit_connector(self, raw: object) -> ConnectorState:
        if isinstance(raw, bool):
            raise LiveSimulatorError("connector must be a positive integer")
        try:
            connector_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise LiveSimulatorError("connector must be a positive integer") from exc
        state = self._connectors.get(connector_id)
        if state is None:
            raise LiveSimulatorError(
                f"connector {connector_id} does not exist; charger has "
                f"{self.config.connectors} connector(s)"
            )
        return state

    def _connector_for_start(self, request: dict[str, Any]) -> ConnectorState:
        raw = request.get("connector_id")
        if raw is not None:
            state = self._state_for_explicit_connector(raw)
            if state.transaction is not None:
                raise LiveSimulatorError(
                    f"connector {state.connector_id} already has transaction "
                    f"{state.transaction.transaction_id}"
                )
            if state.status != "Available":
                raise LiveSimulatorError(
                    f"connector {state.connector_id} is not available ({state.status})"
                )
            state.status = "Preparing"
            return state
        for state in self._connectors.values():
            if state.transaction is None and state.status == "Available":
                # Reserve synchronously before the first await in _start_transaction
                # so concurrent automatic starts cannot select the same connector.
                state.status = "Preparing"
                return state
        if self.config.connectors == 1:
            current = self._connectors[1].transaction
            if current is not None:
                raise LiveSimulatorError(
                    f"connector 1 already has transaction {current.transaction_id}"
                )
        raise LiveSimulatorError("charger has no available connector")

    def _connector_for_active_request(self, request: dict[str, Any]) -> ConnectorState:
        raw = request.get("connector_id")
        if raw is not None:
            state = self._state_for_explicit_connector(raw)
            if state.transaction is None:
                raise LiveSimulatorError(
                    f"connector {state.connector_id} has no active transaction"
                )
            return state
        active = [state for state in self._connectors.values() if state.transaction]
        if not active:
            raise LiveSimulatorError("charger has no active transaction")
        if len(active) > 1:
            raise LiveSimulatorError(
                "charger has multiple active transactions; specify --connector"
            )
        return active[0]

    def _transaction_status(self, state: ConnectorState) -> dict[str, Any]:
        current = state.transaction
        return {
            "connector": state.connector_id,
            "status": state.status,
            "transaction_id": current.transaction_id if current else None,
            "meter_wh": state.meter_wh,
            "power_w": state.power_w,
            "current_a": state.current_a,
            "voltage_v": state.voltage_v,
            "started_at": current.started_at if current else None,
        }

    async def _send_status(self, state: ConnectorState, status: str) -> None:
        await self._simulator.call(
            "StatusNotification",
            {
                "connectorId": state.connector_id,
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

    def _accumulate_meter(self, state: ConnectorState) -> None:
        now = self._clock.now()
        anchor = state.meter_anchor or now
        elapsed_seconds = max(0.0, (now - anchor).total_seconds())
        if state.transaction is not None and state.power_w > 0 and elapsed_seconds > 0:
            state.meter_wh_exact += state.power_w * elapsed_seconds / 3600.0
        state.meter_anchor = now

    def _set_meter_energy(self, state: ConnectorState, energy_wh: object) -> None:
        value = self._nonnegative(energy_wh, name="energy_wh")
        if state.transaction is not None and value < state.transaction.meter_start:
            raise LiveSimulatorError("energy_wh cannot be below meter_start")
        if value < state.meter_wh_exact:
            raise LiveSimulatorError("energy_wh cannot decrease the cumulative meter")
        state.meter_wh_exact = value

    def _set_electrical_state(
        self, state: ConnectorState, request: dict[str, Any]
    ) -> None:
        if request.get("power_w") is not None:
            state.power_w = self._nonnegative(request["power_w"], name="power_w")
        if request.get("current_a") is not None:
            state.current_a = self._nonnegative(request["current_a"], name="current_a")
        if request.get("voltage_v") is not None:
            state.voltage_v = self._nonnegative(request["voltage_v"], name="voltage_v")

    def _meter_values_payload(
        self, state: ConnectorState, timestamp: str
    ) -> dict[str, object]:
        current = state.transaction
        if current is None:
            raise LiveSimulatorError(
                f"connector {state.connector_id} has no active transaction"
            )
        sampled: list[dict[str, str]] = [
            {
                "value": str(state.meter_wh),
                "measurand": "Energy.Active.Import.Register",
                "unit": "Wh",
            },
            {
                "value": str(state.power_w),
                "measurand": "Power.Active.Import",
                "unit": "W",
            },
        ]
        if state.current_a is not None:
            sampled.append(
                {
                    "value": str(state.current_a),
                    "measurand": "Current.Import",
                    "unit": "A",
                }
            )
        if state.voltage_v is not None:
            sampled.append(
                {
                    "value": str(state.voltage_v),
                    "measurand": "Voltage",
                    "unit": "V",
                }
            )
        return {
            "connectorId": state.connector_id,
            "transactionId": current.transaction_id,
            "meterValue": [{"timestamp": timestamp, "sampledValue": sampled}],
        }

    async def _meter(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self._connector_for_active_request(request)
        current = state.transaction
        assert current is not None
        request_id = self._begin_request(
            "MeterValues",
            transaction_id=current.transaction_id,
            connector=state.connector_id,
        )
        try:
            if request.get("energy_wh") is None:
                self._accumulate_meter(state)
            else:
                # Explicit cumulative energy is an authoritative logical sample.
                # Reset the wall-clock anchor without adding an extra fraction.
                state.meter_anchor = self._clock.now()
                self._set_meter_energy(state, request["energy_wh"])
            self._set_electrical_state(state, request)
            timestamp = self._clock.isoformat()
            payload = self._meter_values_payload(state, timestamp)
            async with self._transport_lock:
                await self._simulator.call("MeterValues", payload)
        except Exception as exc:
            self._complete_request(request_id, "MeterValues", error=str(exc))
            self._persist_transaction_state(state)
            raise
        self._persist_transaction_state(state)
        self._complete_request(
            request_id,
            "MeterValues",
            error=None,
            transaction_id=current.transaction_id,
            connector=state.connector_id,
            meter_wh=state.meter_wh,
        )
        return {
            "ok": True,
            "charger": self.config.charger,
            "request_id": request_id,
            "metered": True,
            "timestamp": timestamp,
            **self._transaction_status(state),
        }

    async def _start_transaction(self, request: dict[str, Any]) -> dict[str, Any]:
        id_tag = str(request.get("id_tag", "")).strip()
        if not id_tag:
            raise LiveSimulatorError("transaction start requires an idTag")
        state = self._connector_for_start(request)
        request_id = self._begin_request(
            "StartTransaction",
            connector=state.connector_id,
            id_tag_sha256=self._requests.id_tag_fingerprint(id_tag),
        )
        try:
            self._accumulate_meter(state)
            meter_start = self._meter_value(
                request.get("meter_start", state.meter_wh), name="meter_start"
            )
            if meter_start < state.meter_wh:
                raise LiveSimulatorError("meter_start cannot decrease the cumulative meter")
            started_at = self._clock.isoformat()

            async with self._transport_lock:
                await self._send_status(state, "Preparing")
                try:
                    response = await self._simulator.call(
                        "StartTransaction",
                        {
                            "connectorId": state.connector_id,
                            "idTag": id_tag,
                            "meterStart": meter_start,
                            "timestamp": started_at,
                        },
                    )
                except Exception:
                    state.status = "Available"
                    try:
                        await self._send_status(state, "Available")
                    except Exception:
                        pass
                    raise

                transaction_id = response.get("transactionId")
                info = response.get("idTagInfo")
                if not isinstance(info, dict):
                    state.status = "Available"
                    try:
                        await self._send_status(state, "Available")
                    except Exception:
                        pass
                    raise LiveSimulatorError(
                        "StartTransaction response requires idTagInfo.status"
                    )
                authorization = info.get("status")
                if not isinstance(authorization, str) or not authorization:
                    state.status = "Available"
                    try:
                        await self._send_status(state, "Available")
                    except Exception:
                        pass
                    raise LiveSimulatorError(
                        "StartTransaction response requires idTagInfo.status"
                    )
                if authorization != "Accepted":
                    state.status = "Available"
                    await self._send_status(state, "Available")
                    self._persist_transaction_state(state)
                    self._complete_request(
                        request_id,
                        "StartTransaction",
                        error=None,
                        authorization=authorization,
                        started=False,
                        connector=state.connector_id,
                    )
                    return {
                        "ok": True,
                        "charger": self.config.charger,
                        "request_id": request_id,
                        "started": False,
                        "authorization": authorization,
                        **self._transaction_status(state),
                    }
                if isinstance(transaction_id, bool) or not isinstance(transaction_id, int):
                    state.status = "Available"
                    try:
                        await self._send_status(state, "Available")
                    except Exception:
                        pass
                    raise LiveSimulatorError(
                        "StartTransaction response requires integer transactionId"
                    )

                state.transaction = ActiveTransaction(
                    transaction_id=transaction_id,
                    connector_id=state.connector_id,
                    id_tag=id_tag,
                    meter_start=meter_start,
                    started_at=started_at,
                )
                state.meter_wh_exact = float(meter_start)
                state.meter_anchor = self._clock.now()
                state.status = "Charging"
                await self._send_status(state, "Charging")
        except Exception as exc:
            if state.transaction is None:
                state.status = "Available"
            self._complete_request(request_id, "StartTransaction", error=str(exc))
            self._persist_transaction_state(state)
            raise

        self._persist_transaction_state(state)
        self._complete_request(
            request_id,
            "StartTransaction",
            error=None,
            authorization=authorization,
            started=True,
            connector=state.connector_id,
            transaction_id=transaction_id,
            meter_wh=state.meter_wh,
        )
        return {
            "ok": True,
            "charger": self.config.charger,
            "request_id": request_id,
            "started": True,
            "authorization": authorization,
            **self._transaction_status(state),
        }

    async def _stop_transaction(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self._connector_for_active_request(request)
        current = state.transaction
        assert current is not None
        request_id = self._begin_request(
            "StopTransaction",
            transaction_id=current.transaction_id,
            connector=state.connector_id,
        )
        self._accumulate_meter(state)
        accumulated_meter = state.meter_wh_exact
        try:
            if request.get("meter_stop") is not None:
                meter_stop = self._meter_value(request["meter_stop"], name="meter_stop")
                if meter_stop < state.meter_wh:
                    raise LiveSimulatorError("meter_stop cannot decrease the cumulative meter")
                state.meter_wh_exact = float(meter_stop)
            meter_stop = state.meter_wh
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
                await self._send_status(state, "Finishing")
                state.status = "Finishing"
                try:
                    response = await self._simulator.call("StopTransaction", payload)
                except Exception:
                    state.meter_wh_exact = accumulated_meter
                    state.status = "Charging"
                    try:
                        await self._send_status(state, "Charging")
                    except Exception:
                        pass
                    raise

                info = response.get("idTagInfo")
                authorization = info.get("status") if isinstance(info, dict) else None
                transaction_id = current.transaction_id
                state.transaction = None
                state.power_w = 0.0
                state.meter_anchor = self._clock.now()
                state.status = "Available"
                self._persist_transaction_state(state)
                await self._send_status(state, "Available")
        except Exception as exc:
            self._complete_request(request_id, "StopTransaction", error=str(exc))
            self._persist_transaction_state(state)
            raise

        self._persist_transaction_state(state)
        self._complete_request(
            request_id,
            "StopTransaction",
            error=None,
            connector=state.connector_id,
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
            "connector": state.connector_id,
            "status": state.status,
        }
