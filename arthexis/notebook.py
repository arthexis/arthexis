from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


class ArthexisNotebookError(RuntimeError):
    """Raised when notebook helper operations fail."""


@dataclass(frozen=True)
class Charger:
    """Typed wrapper around a charger payload returned by Arthexis APIs."""

    payload: dict[str, Any]

    @property
    def charger_id(self) -> str | None:
        return self.payload.get("charger_id")

    @property
    def status(self) -> str | None:
        return self.payload.get("status")

    @property
    def connected(self) -> bool | None:
        return self.payload.get("connected")

    @property
    def transaction(self) -> dict[str, Any] | None:
        return self.payload.get("transaction")


class Node:
    """Notebook-friendly client for querying a live Arthexis node."""

    def __init__(
        self,
        base_url: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def login(self, username: str, password: str) -> None:
        login_url = f"{self.base_url}/admin/login/"
        csrf = self._fetch_login_csrf(login_url)
        response = self.session.post(
            login_url,
            data={
                "username": username,
                "password": password,
                "csrfmiddlewaretoken": csrf,
                "next": "/admin/",
            },
            headers={"Referer": login_url},
            timeout=self.timeout,
        )
        response.raise_for_status()
        if not self.is_authenticated:
            raise ArthexisNotebookError("Login failed: invalid credentials or CSRF flow.")

    @property
    def is_authenticated(self) -> bool:
        return "sessionid" in self.session.cookies

    def chargers(self) -> list[Charger]:
        payload = self._get_json("/ocpp/chargers/")
        return [Charger(item) for item in payload.get("chargers", [])]

    def charger(self, charger_id: str) -> Charger:
        payload = self._get_json(f"/ocpp/chargers/{charger_id}/")
        return Charger(payload)

    def _fetch_login_csrf(self, login_url: str) -> str:
        response = self.session.get(login_url, timeout=self.timeout)
        response.raise_for_status()
        token = self._extract_csrf_token(response.text)
        if not token:
            raise ArthexisNotebookError("Could not find csrfmiddlewaretoken on admin login page.")
        return token

    @staticmethod
    def _extract_csrf_token(html: str) -> str | None:
        marker = 'name="csrfmiddlewaretoken"'
        marker_index = html.find(marker)
        if marker_index < 0:
            return None

        value_marker = 'value="'
        value_start = html.find(value_marker, marker_index)
        if value_start < 0:
            return None

        value_start += len(value_marker)
        value_end = html.find('"', value_start)
        if value_end < 0:
            return None

        return html[value_start:value_end]

    def _get_json(self, path: str) -> dict[str, Any]:
        response = self.session.get(f"{self.base_url}{path}", timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ArthexisNotebookError("Unexpected API response; expected JSON object.")
        return data


__all__ = ["ArthexisNotebookError", "Charger", "Node"]
