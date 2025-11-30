import base64
import json

import requests
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory
from requests import RequestException

from apps.nodes.views import node_info, register_node


class Command(BaseCommand):
    help = "Register this node with a remote host using a CLI token."

    def add_arguments(self, parser):
        parser.add_argument(
            "token",
            help="Base64 encoded registration token generated from the Nodes admin.",
        )

    def _decode_token(self, token: str) -> dict:
        try:
            payload = json.loads(base64.urlsafe_b64decode(token).decode("utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            raise CommandError("Invalid registration token") from exc

        required = {"register", "info", "username", "password"}
        missing = sorted(required - set(payload))
        if missing:
            raise CommandError(
                f"Registration token missing required fields: {', '.join(missing)}"
            )
        return payload

    def _request_json(self, session: requests.Session, url: str, *, method: str = "get", json_body=None):
        try:
            response = session.request(method=method, url=url, json=json_body, timeout=10)
        except RequestException as exc:
            raise CommandError(f"Unable to reach {url}: {exc}")

        try:
            data = response.json()
        except ValueError:
            data = {}

        if not response.ok:
            detail = data.get("detail") if isinstance(data, dict) else response.text
            raise CommandError(
                f"Request to {url} failed with status {response.status_code}: {detail}"
            )
        return data

    def _load_local_info(self) -> dict:
        factory = RequestFactory()
        request = factory.get("/nodes/info/")
        response = node_info(request)
        if response.status_code != 200:
            raise CommandError("Unable to load local node information")
        try:
            return json.loads(response.content.decode())
        except Exception as exc:  # pragma: no cover - defensive
            raise CommandError("Local node information payload is invalid") from exc

    def _build_payload(self, info: dict, relation: str | None) -> dict:
        payload = {
            "hostname": info.get("hostname", ""),
            "address": info.get("address", ""),
            "port": info.get("port", 8888),
            "mac_address": info.get("mac_address", ""),
            "public_key": info.get("public_key", ""),
            "features": info.get("features") or [],
            "trusted": True,
        }
        if relation:
            payload["current_relation"] = relation
        for key in (
            "network_hostname",
            "ipv4_address",
            "ipv6_address",
            "installed_version",
            "installed_revision",
        ):
            value = info.get(key)
            if value:
                payload[key] = value
        role_value = info.get("role") or info.get("role_name")
        if isinstance(role_value, str) and role_value.strip():
            payload["role"] = role_value.strip()
        return payload

    def _register_host_locally(self, payload: dict):
        User = get_user_model()
        local_user = (
            User.all_objects.filter(is_superuser=True).first()
            if hasattr(User, "all_objects")
            else User.objects.filter(is_superuser=True).first()
        )
        if not local_user:
            raise CommandError("A superuser is required to complete local registration")

        factory = RequestFactory()
        request = factory.post(
            "/nodes/register/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.user = local_user
        request._cached_user = local_user
        response = register_node(request)
        if response.status_code != 200:
            try:
                detail = json.loads(response.content.decode()).get("detail", "")
            except Exception:
                detail = response.content.decode(errors="ignore")
            raise CommandError(
                f"Local registration failed with status {response.status_code}: {detail}"
            )

    def handle(self, *args, **options):
        payload = self._decode_token(options["token"])
        session = requests.Session()
        session.auth = (payload["username"], payload["password"])

        host_info = self._request_json(session, payload["info"])
        visitor_info = self._load_local_info()

        visitor_payload = self._build_payload(visitor_info, "Downstream")
        visitor_payload["deactivate_user"] = True
        host_result = self._request_json(
            session,
            payload["register"],
            method="post",
            json_body=visitor_payload,
        )
        if not isinstance(host_result, dict) or not host_result.get("id"):
            raise CommandError("Remote registration did not return a node identifier")

        host_payload = self._build_payload(host_info, "Upstream")
        self._register_host_locally(host_payload)

        self.stdout.write(self.style.SUCCESS("Registration completed successfully."))
