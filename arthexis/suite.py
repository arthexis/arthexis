from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import socket
from pathlib import Path
from typing import Any, Dict, List

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

DEFAULT_TIMEOUT = 15
REGISTER_ENDPOINT = "/nodes/register/"
PROXY_EXECUTE_ENDPOINT = "/nodes/proxy/execute/"
INTERFACE_ROLE = "Interface"


class SuiteError(RuntimeError):
    """Raised when suite gateway operations fail."""


class RemoteObject:
    """Simple wrapper around serialized Django model objects."""

    def __init__(self, payload: Dict[str, Any]):
        self.model = payload.get("model")
        self.pk = payload.get("pk")
        self.fields = payload.get("fields", {})

    def __getattr__(self, name: str) -> Any:
        if name in self.fields:
            return self.fields[name]
        raise AttributeError(name)

    def to_dict(self) -> Dict[str, Any]:
        data = {"model": self.model, "pk": self.pk}
        data.update(self.fields)
        return data

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        field_preview = ", ".join(f"{k}={v!r}" for k, v in list(self.fields.items())[:3])
        return f"<RemoteObject {self.model}({field_preview})>"


class SuiteModelProxy:
    """Proxy for a remote model exposed through the suite gateway."""

    def __init__(self, gateway: "SuiteGateway", meta: Dict[str, Any]):
        self.gateway = gateway
        self.app_label = meta.get("app_label")
        self.model_name = meta.get("model")
        self.suite_name = meta.get("suite_name") or meta.get("object_name")

    def objects(self, **filters: Any) -> List[RemoteObject]:
        response = self.gateway._execute(
            self.app_label,
            self.model_name,
            action="list",
            filters=filters or None,
        )
        payload = response.get("objects", [])
        return [RemoteObject(item) for item in payload]

    def get(self, **lookup: Any) -> RemoteObject:
        response = self.gateway._execute(
            self.app_label,
            self.model_name,
            action="get",
            filters=lookup or None,
        )
        payload = response.get("object")
        if not payload:
            raise SuiteError("Object not found")
        return RemoteObject(payload)


class SuiteGateway:
    """Client for interacting with a remote Arthexis suite."""

    def __init__(self) -> None:
        self._session: requests.Session | None = None
        self._base_url: str = ""
        self._private_key = None
        self._node_uuid: str = ""
        self._username: str = ""
        self._password: str = ""
        self._catalog: dict[str, Dict[str, Any]] = {}
        self._aliases: dict[str, str] = {}
        self._proxies: dict[str, SuiteModelProxy] = {}
        self._connected = False
        self._role = INTERFACE_ROLE

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(
        self,
        host: str,
        user: str,
        password: str,
        node_pkey: str | bytes | Path,
        *,
        port: int | None = None,
        role: str | None = None,
    ) -> "SuiteGateway":
        """Connect to a remote suite and register the interface node."""

        self._session = requests.Session()
        self._base_url = self._normalize_host(host, port)
        self._username = user
        self._password = password
        if role:
            self._role = role
        self._private_key = self._load_private_key(node_pkey)
        public_key = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        payload = self._build_register_payload(public_key)
        self._register_node(payload)
        self._connected = True
        self._catalog.clear()
        self._aliases.clear()
        self._proxies.clear()
        return self

    def _normalize_host(self, host: str, port: int | None) -> str:
        base = host.strip()
        if not base:
            raise SuiteError("Host is required")
        if not base.startswith(("http://", "https://")):
            base = f"https://{base}"
        base = base.rstrip("/")
        if port and ":" not in base.split("//", 1)[-1]:
            base = f"{base}:{port}"
        return base

    def _load_private_key(self, value: str | bytes | Path):
        if isinstance(value, Path):
            key_bytes = value.read_bytes()
        elif isinstance(value, (str, os.PathLike)):
            path = Path(value)
            if path.exists():
                key_bytes = path.read_bytes()
            else:
                key_bytes = str(value).encode()
        elif isinstance(value, bytes):
            key_bytes = value
        else:  # pragma: no cover - defensive
            raise SuiteError("Unsupported private key format")
        try:
            return serialization.load_pem_private_key(key_bytes, password=None)
        except Exception as exc:  # pragma: no cover - defensive
            raise SuiteError(f"Unable to load private key: {exc}") from exc

    def _build_register_payload(self, public_key: str) -> Dict[str, Any]:
        hostname = socket.gethostname() or "interface-node"
        try:
            address = socket.gethostbyname(hostname)
        except OSError:
            address = "127.0.0.1"
        mac_address = self._generate_mac()
        token = secrets.token_hex(16)
        signature = base64.b64encode(
            self._private_key.sign(
                token.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        ).decode()
        return {
            "hostname": hostname,
            "address": address,
            "port": 0,
            "mac_address": mac_address,
            "public_key": public_key,
            "token": token,
            "signature": signature,
            "role": self._role,
        }

    def _generate_mac(self) -> str:
        seed = f"{self._username}@{self._base_url}"
        digest = hashlib.sha256(seed.encode()).hexdigest()[:12]
        return ":".join(digest[i : i + 2] for i in range(0, 12, 2))

    def _register_node(self, payload: Dict[str, Any]) -> None:
        response = self._post(REGISTER_ENDPOINT, payload)
        data = self._parse_json(response)
        uuid_value = data.get("uuid") or ""
        if not uuid_value:
            raise SuiteError("Remote registration did not return a node UUID")
        self._node_uuid = uuid_value

    def _post(self, path: str, payload: Dict[str, Any], headers: Dict[str, str] | None = None):
        if not self._session:
            raise SuiteError("Gateway session is unavailable")
        url = f"{self._base_url}{path}"
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        try:
            response = self._session.post(
                url,
                data=body,
                headers=request_headers,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:  # pragma: no cover - network errors
            raise SuiteError(f"Request failed: {exc}") from exc
        if not response.ok:
            raise SuiteError(f"Request failed: {response.status_code} {response.text}")
        response._body = body  # type: ignore[attr-defined]
        return response

    def _parse_json(self, response: requests.Response) -> Dict[str, Any]:
        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover - defensive
            raise SuiteError("Invalid JSON response") from exc

    def _execute(
        self,
        app_label: str | None,
        model_name: str | None,
        *,
        action: str,
        filters: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if not self._connected:
            raise SuiteError("Suite gateway is not connected")
        payload: Dict[str, Any] = {
            "requester": self._node_uuid,
            "action": action,
            "credentials": {
                "username": self._username,
                "password": self._password,
            },
        }
        if model_name and app_label:
            payload["model"] = f"{app_label}.{model_name}"
        if filters:
            payload["filters"] = filters
        headers = {
            "X-Signature": base64.b64encode(
                self._private_key.sign(
                    json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(),
                    padding.PKCS1v15(),
                    hashes.SHA256(),
                )
            ).decode()
        }
        response = self._post(PROXY_EXECUTE_ENDPOINT, payload, headers=headers)
        return self._parse_json(response)

    def _ensure_catalog(self) -> None:
        if self._catalog:
            return
        data = self._execute(None, None, action="schema")
        models = data.get("models", [])
        if not models:
            raise SuiteError("Remote suite did not return any models")
        for entry in models:
            suite_name = entry.get("suite_name")
            if not suite_name:
                continue
            self._catalog[suite_name] = entry
            object_name = entry.get("object_name")
            if object_name:
                self._aliases.setdefault(object_name, suite_name)

    def __getattr__(self, name: str) -> SuiteModelProxy:
        if name.startswith("_"):
            raise AttributeError(name)
        self._ensure_catalog()
        entry = self._catalog.get(name)
        if entry is None:
            suite_name = self._aliases.get(name)
            if suite_name:
                entry = self._catalog.get(suite_name)
        if entry is None:
            raise AttributeError(name)
        suite_name = entry.get("suite_name") or name
        proxy = self._proxies.get(suite_name)
        if proxy is None:
            proxy = SuiteModelProxy(self, entry)
            self._proxies[suite_name] = proxy
        return proxy


suite = SuiteGateway()

__all__ = ["SuiteGateway", "suite", "RemoteObject", "SuiteError"]
