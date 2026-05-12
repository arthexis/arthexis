from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from django.conf import settings
import requests

from config.admin_urls import normalize_admin_url_path


CSRF_TOKEN_PATTERN = re.compile(
    r"<input\b"
    r"(?=[^>]*\bname=[\"']csrfmiddlewaretoken[\"'])"
    r"(?=[^>]*\bvalue=[\"'](?P<token>[A-Za-z0-9]+)[\"'])"
    r"[^>]*>",
    re.IGNORECASE,
)
DEFAULT_ADMIN_URL_PATH = "admin/"


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
        admin_path: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.admin_path = normalize_admin_url_path(admin_path or _default_admin_url_path())

    def login(self, username: str, password: str) -> None:
        login_url = self._absolute_url(f"/{self.admin_path}login/")
        csrf = self._fetch_login_csrf(login_url)
        response = self.session.post(
            login_url,
            data={
                "username": username,
                "password": password,
                "csrfmiddlewaretoken": csrf,
                "next": f"/{self.admin_path}",
            },
            headers={"Referer": login_url},
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ArthexisNotebookError("Login request failed.") from exc
        if not self._admin_session_is_authenticated():
            raise ArthexisNotebookError("Login failed: invalid credentials or CSRF flow.")

    @property
    def is_authenticated(self) -> bool:
        return "sessionid" in self.session.cookies

    def chargers(self) -> list[Charger]:
        payload = self._get_json("/ocpp/chargers/")
        return [Charger(item) for item in payload.get("chargers", [])]

    def charger(self, charger_id: str) -> Charger:
        payload = self._get_json(f"/ocpp/chargers/{quote(charger_id, safe='')}/")
        return Charger(payload)

    def _fetch_login_csrf(self, login_url: str) -> str:
        try:
            response = self.session.get(login_url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ArthexisNotebookError("Could not fetch admin login page.") from exc
        token = self._extract_csrf_token(response.text)
        if not token:
            raise ArthexisNotebookError("Could not find csrfmiddlewaretoken on admin login page.")
        return token

    @staticmethod
    def _extract_csrf_token(html: str) -> str | None:
        match = CSRF_TOKEN_PATTERN.search(html)
        return match.group("token") if match else None

    def _get_json(self, path: str) -> dict[str, Any]:
        try:
            response = self.session.get(self._absolute_url(path), timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise ArthexisNotebookError(f"Request failed for {path!r}.") from exc
        except ValueError as exc:
            raise ArthexisNotebookError(f"Invalid JSON response for {path!r}.") from exc
        if not isinstance(data, dict):
            raise ArthexisNotebookError("Unexpected API response; expected JSON object.")
        return data

    def _admin_session_is_authenticated(self) -> bool:
        try:
            response = self.session.get(
                self._absolute_url(f"/{self.admin_path}"),
                timeout=self.timeout,
                allow_redirects=False,
            )
            response.raise_for_status()
        except requests.RequestException:
            return False
        return not 300 <= response.status_code < 400

    def _absolute_url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"


def _default_admin_url_path() -> str:
    if settings.configured:
        return str(getattr(settings, "ADMIN_URL_PATH", DEFAULT_ADMIN_URL_PATH))
    return DEFAULT_ADMIN_URL_PATH


__all__ = ["ArthexisNotebookError", "Charger", "Node"]
