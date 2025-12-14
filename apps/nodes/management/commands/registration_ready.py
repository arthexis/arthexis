import json
from secrets import token_hex

from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.urls import reverse

from apps.nodes.models import Node


def _run_registration_checks(command: BaseCommand) -> None:
    node, created = Node.register_current()
    ready = True

    if created:
        command.stdout.write(
            command.style.SUCCESS(
                f"Registered current node as {node.hostname}:{node.port}."
            )
        )
    else:
        command.stdout.write(
            f"Current node record refreshed ({node.hostname}:{node.port})."
        )

    security_dir = node.get_base_path() / "security"
    priv_path = security_dir / f"{node.public_endpoint}"
    pub_path = security_dir / f"{node.public_endpoint}.pub"

    missing_files = [path.name for path in (priv_path, pub_path) if not path.exists()]
    if missing_files:
        ready = False
        command.stderr.write(
            command.style.ERROR(
                "Missing security key files: " + ", ".join(sorted(missing_files))
            )
        )
    else:
        command.stdout.write(command.style.SUCCESS("Security key files are present."))

    if node.public_key:
        command.stdout.write(
            command.style.SUCCESS("Public key is stored in the database.")
        )
    else:
        ready = False
        command.stderr.write(command.style.ERROR("Public key is not stored in the database."))

    client = Client()
    token = token_hex(16)

    info_response = client.get(reverse("node-info"), {"token": token})
    if info_response.status_code != 200:
        ready = False
        command.stderr.write(
            command.style.ERROR(
                f"/nodes/info/ returned status {info_response.status_code}."
            )
        )
        info_data = {}
    else:
        command.stdout.write(
            command.style.SUCCESS("Local /nodes/info/ endpoint responded successfully.")
        )
        try:
            info_data = info_response.json()
        except ValueError:
            ready = False
            command.stderr.write(
                command.style.ERROR("/nodes/info/ did not return valid JSON data.")
            )
            info_data = {}

    if info_data:
        if info_data.get("token_signature"):
            command.stdout.write(command.style.SUCCESS("Token signing is available."))
        else:
            ready = False
            command.stderr.write(
                command.style.ERROR(
                    "Token signing is unavailable. The private key may be missing or unreadable."
                )
            )

    register_url = reverse("register-node")
    options_response = client.options(
        register_url,
        HTTP_ORIGIN="https://example.com",
    )
    if (
        options_response.status_code == 200
        and options_response.get("Access-Control-Allow-Origin") == "https://example.com"
    ):
        command.stdout.write(
            command.style.SUCCESS("CORS preflight for /nodes/register/ succeeded.")
        )
    else:
        ready = False
        command.stderr.write(
            command.style.ERROR("CORS preflight for /nodes/register/ failed.")
        )

    if info_data.get("token_signature"):
        payload = {
            "hostname": info_data.get("hostname"),
            "address": info_data.get("address"),
            "port": info_data.get("port"),
            "mac_address": info_data.get("mac_address"),
            "public_key": info_data.get("public_key"),
            "token": token,
            "signature": info_data.get("token_signature"),
        }
        if "features" in info_data:
            payload["features"] = info_data["features"]

        register_response = client.post(
            register_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_ORIGIN="https://example.com",
        )
        if register_response.status_code == 200:
            command.stdout.write(
                command.style.SUCCESS(
                    "Signed registration request completed successfully."
                )
            )
        else:
            ready = False
            command.stderr.write(
                command.style.ERROR(
                    "Signed registration request failed with status "
                    f"{register_response.status_code}: {register_response.content.decode(errors='ignore')}"
                )
            )
    else:
        ready = False
        command.stderr.write(
            command.style.ERROR(
                "Skipping signed registration test because token signing is unavailable."
            )
        )

    if not ready:
        raise CommandError(
            "Visitor registration is not ready. Review the errors above and retry."
        )

    command.stdout.write(command.style.SUCCESS("Visitor registration checks passed."))


class Command(BaseCommand):
    help = "Verify that this node is ready to register with a host it visits."

    def handle(self, *args, **options):
        return _run_registration_checks(self)
