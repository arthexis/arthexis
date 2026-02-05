import re
import textwrap
import uuid
from urllib.parse import urlsplit, urlunsplit

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Generate a curl-based visitor registration script."
    TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

    def add_arguments(self, parser):
        parser.add_argument(
            "upstream",
            help="Base URL for the upstream node (e.g. https://host:8888).",
        )
        parser.add_argument(
            "--local-base",
            default="https://localhost:8888",
            help="Base URL for the local node (default: https://localhost:8888).",
        )
        parser.add_argument(
            "--token",
            default="",
            help="Optional registration token to reuse (default: generate a new token).",
        )

    def _normalize_base_url(self, raw: str, *, label: str) -> str:
        if not raw:
            raise CommandError(f"{label} base URL is required.")
        candidate = raw.strip()
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlsplit(candidate)
        if not parsed.scheme or not parsed.netloc:
            raise CommandError(f"{label} base URL is invalid: {raw}")
        if parsed.scheme != "https":
            raise CommandError(f"{label} base URL must use https: {raw}")
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")

    def handle(self, *args, **options):
        upstream_base = self._normalize_base_url(
            options["upstream"], label="Upstream"
        )
        local_base = self._normalize_base_url(options["local_base"], label="Local")
        token = options["token"] or uuid.uuid4().hex
        if not self.TOKEN_PATTERN.match(token):
            raise CommandError(
                "Token must contain only alphanumeric characters, hyphens, or underscores."
            )

        script = textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail

            TOKEN="{token}"
            UPSTREAM_INFO="{upstream_base}/nodes/info/"
            UPSTREAM_REGISTER="{upstream_base}/nodes/register/"
            LOCAL_INFO="{local_base}/nodes/info/"
            LOCAL_REGISTER="{local_base}/nodes/register/"

            downstream_payload="$(
              curl -fsSL "${{LOCAL_INFO}}?token=${{TOKEN}}" | \\
                TOKEN="${{TOKEN}}" RELATION="Downstream" python - <<'PY'
            import json
            import os
            import sys

            data = json.load(sys.stdin)
            token = os.environ["TOKEN"]
            relation = os.environ.get("RELATION")
            signature = data.get("token_signature")
            if not signature:
                raise SystemExit("token_signature missing from /nodes/info/")

            payload = {{
                "hostname": data.get("hostname", ""),
                "address": data.get("address", ""),
                "port": data.get("port", 8888),
                "mac_address": data.get("mac_address", ""),
                "public_key": data.get("public_key", ""),
                "token": token,
                "signature": signature,
                "trusted": True,
            }}
            if relation:
                payload["current_relation"] = relation
            for key in (
                "network_hostname",
                "ipv4_address",
                "ipv6_address",
                "installed_version",
                "installed_revision",
                "role",
                "base_site_domain",
            ):
                value = data.get(key)
                if value:
                    payload[key] = value
            if "features" in data:
                payload["features"] = data["features"]

            print(json.dumps(payload))
            PY
            )"

            curl -fsSL -X POST "${{UPSTREAM_REGISTER}}" \\
              -H "Content-Type: application/json" \\
              -d "${{downstream_payload}}"

            upstream_payload="$(
              curl -fsSL "${{UPSTREAM_INFO}}?token=${{TOKEN}}" | \\
                TOKEN="${{TOKEN}}" RELATION="Upstream" python - <<'PY'
            import json
            import os
            import sys

            data = json.load(sys.stdin)
            token = os.environ["TOKEN"]
            relation = os.environ.get("RELATION")
            signature = data.get("token_signature")
            if not signature:
                raise SystemExit("token_signature missing from /nodes/info/")

            payload = {{
                "hostname": data.get("hostname", ""),
                "address": data.get("address", ""),
                "port": data.get("port", 8888),
                "mac_address": data.get("mac_address", ""),
                "public_key": data.get("public_key", ""),
                "token": token,
                "signature": signature,
                "trusted": True,
            }}
            if relation:
                payload["current_relation"] = relation
            for key in (
                "network_hostname",
                "ipv4_address",
                "ipv6_address",
                "installed_version",
                "installed_revision",
                "role",
                "base_site_domain",
            ):
                value = data.get(key)
                if value:
                    payload[key] = value
            if "features" in data:
                payload["features"] = data["features"]

            print(json.dumps(payload))
            PY
            )"

            curl -fsSL -X POST "${{LOCAL_REGISTER}}" \\
              -H "Content-Type: application/json" \\
              -d "${{upstream_payload}}"
            """
        )
        self.stdout.write(script)
