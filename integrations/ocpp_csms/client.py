from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any


SCHEMAS = {
    "status": "ocpp-csms/status/v1",
    "transactions": "ocpp-csms/transactions/v1",
    "energy": "ocpp-csms/energy/v1",
    "events": "ocpp-csms/events/v1",
}


class OcppCsmsError(RuntimeError):
    """Base error for the local OCPP-CSMS integration."""


class OcppCsmsUnavailable(OcppCsmsError):
    """The local OCPP-CSMS command could not be executed."""


class OcppCsmsTimeout(OcppCsmsError):
    """The local OCPP-CSMS command did not finish in time."""


class OcppCsmsCommandError(OcppCsmsError):
    """The local OCPP-CSMS command returned a non-zero exit code."""

    def __init__(self, returncode: int, stderr: str):
        detail = stderr.strip() or "no diagnostic output"
        super().__init__(f"OCPP-CSMS command failed with exit code {returncode}: {detail}")
        self.returncode = returncode
        self.stderr = stderr


class OcppCsmsContractError(OcppCsmsError):
    """The command returned data that does not match the supported contract."""


class LocalCliClient:
    """Read normalized OCPP-CSMS state through its stable local CLI contracts."""

    def __init__(
        self,
        *,
        executable: str = "ocpp-csms",
        data_dir: str | Path | None = None,
        timeout: float = 5.0,
    ):
        self.executable = executable
        self.data_dir = str(data_dir) if data_dir is not None else None
        self.timeout = timeout

    def status(self, charger: str | None = None, *, charging: bool = False) -> dict[str, Any]:
        args = ["status"]
        if charger is not None:
            args.append(charger)
        if charging:
            args.append("--charging")
        args.append("--json")
        return self._read("status", args)

    def transactions(
        self,
        transaction_id: int | None = None,
        *,
        active: bool = False,
        last: bool = False,
        charger: str | None = None,
        connector: int | None = None,
        id_tag: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if active and last:
            raise ValueError("active and last are mutually exclusive")

        args = ["transactions"]
        if transaction_id is not None:
            args.append(str(transaction_id))
        if active:
            args.append("--active")
        if last:
            args.append("--last")
        self._option(args, "--charger", charger)
        self._option(args, "--connector", connector)
        self._option(args, "--id-tag", id_tag)
        self._option(args, "--since", since)
        self._option(args, "--until", until)
        self._option(args, "--limit", limit)
        args.append("--json")
        return self._read("transactions", args)

    def energy(
        self,
        *,
        charger: str | None = None,
        connector: int | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        args = ["energy"]
        self._option(args, "--charger", charger)
        self._option(args, "--connector", connector)
        self._option(args, "--since", since)
        self._option(args, "--until", until)
        args.append("--json")
        return self._read("energy", args)

    def events(
        self,
        charger: str | None = None,
        *,
        transaction: int | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        args = ["events"]
        if charger is not None:
            args.append(charger)
        self._option(args, "--transaction", transaction)
        self._option(args, "--since", since)
        self._option(args, "--until", until)
        self._option(args, "--limit", limit)
        args.append("--json")
        return self._read("events", args)

    @staticmethod
    def _option(args: list[str], flag: str, value: object | None) -> None:
        if value is not None:
            args.extend((flag, str(value)))

    def _command(self, args: Sequence[str]) -> list[str]:
        command = [self.executable]
        if self.data_dir is not None:
            command.extend(("--data-dir", self.data_dir))
        command.extend(args)
        return command

    def _read(self, contract: str, args: Sequence[str]) -> dict[str, Any]:
        command = self._command(args)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise OcppCsmsUnavailable(f"OCPP-CSMS command not found: {self.executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise OcppCsmsTimeout(
                f"OCPP-CSMS command timed out after {self.timeout:g} seconds"
            ) from exc

        if result.returncode != 0:
            raise OcppCsmsCommandError(result.returncode, result.stderr)

        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise OcppCsmsContractError("OCPP-CSMS returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise OcppCsmsContractError("OCPP-CSMS contract envelope must be an object")

        expected_schema = SCHEMAS[contract]
        actual_schema = payload.get("schema")
        if actual_schema != expected_schema:
            raise OcppCsmsContractError(
                f"Unsupported OCPP-CSMS contract schema: expected {expected_schema!r}, "
                f"got {actual_schema!r}"
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise OcppCsmsContractError("OCPP-CSMS contract data must be an object")
        return data
