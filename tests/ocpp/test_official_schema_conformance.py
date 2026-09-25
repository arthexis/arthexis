import json
import os
from pathlib import Path

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone

from apps.ocpp.models import Charger, OcppTransaction
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions as V16InboundActions
from apps.ocpp.protocol.v201.inbound import InboundActions as V201InboundActions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.ocpp.support import load_schema, minimal_instance

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(scope="module")
def conformance_environment():
    root = os.environ.get("OCPP_CONFORMANCE_SCHEMA_ROOT")
    if not root:
        pytest.skip("official OCPP schemas are not prepared")

    import jsonschema

    return Path(root), jsonschema


def schema(
    schema_root: Path,
    version: str,
    action: str,
    kind: str,
) -> dict[str, object]:
    return load_schema(schema_root / version / f"{action}{kind.title()}.json")


def validate(payload: object, selected_schema: dict[str, object], jsonschema) -> None:
    jsonschema.validate(
        payload,
        selected_schema,
        format_checker=jsonschema.FormatChecker(),
    )


def seed_business_state(
    *,
    version: str,
    action: str,
    charger: Charger,
    payload: dict[str, object],
) -> None:
    if action == "MeterValues":
        for meter_value in payload.get("meterValue", []):
            if not isinstance(meter_value, dict):
                continue
            for sampled_value in meter_value.get("sampledValue", []):
                if isinstance(sampled_value, dict) and isinstance(
                    sampled_value.get("value"), str
                ):
                    sampled_value["value"] = "0"

    if version == "ocpp16" and action in {"MeterValues", "StopTransaction"}:
        transaction = OcppTransaction.objects.create(
            charger=charger,
            remote_id=f"seed-{action}",
            started_at=timezone.now(),
        )
        payload["transactionId"] = transaction.pk


def exercise_version(
    *,
    schema_root: Path,
    jsonschema,
    version_dir: str,
    protocol: ProtocolVersion,
    resolver_factory,
) -> None:
    json.loads(
        (schema_root / version_dir / "index.json").read_text(encoding="utf-8")
    )
    manifest_name = "ocpp16.json" if version_dir == "ocpp16" else "ocpp201.json"
    manifest = json.loads(
        Path("tests/ocpp/spec", manifest_name).read_text(encoding="utf-8")
    )

    for action in manifest["directions"]["charge_point_to_csms"]:
        request_schema = schema(schema_root, version_dir, action, "request")
        response_schema = schema(schema_root, version_dir, action, "response")
        payload = minimal_instance(request_schema)
        validate(payload, request_schema, jsonschema)

        selected = Charger.objects.create(
            identity=f"{version_dir}-{action}",
            authorization_mode=Charger.AuthorizationMode.OPEN,
        )
        seed_business_state(
            version=version_dir,
            action=action,
            charger=selected,
            payload=payload,
        )
        validate(payload, request_schema, jsonschema)

        resolver = resolver_factory(selected)
        dispatcher = FrameDispatcher(
            charger=selected,
            version=protocol,
            pending_calls=PendingCalls(),
            handler_resolver=resolver.resolve,
        )
        response = async_to_sync(dispatcher.dispatch)(
            Call(unique_id="conformance", action=action, payload=payload)
        )
        assert isinstance(response, CallResult), (
            f"{protocol.value} {action} rejected a schema-valid request: {response!r}"
        )
        validate(response.payload, response_schema, jsonschema)


@pytest.mark.parametrize(
    ("version_dir", "protocol", "resolver_factory"),
    [
        ("ocpp16", ProtocolVersion.OCPP_16, V16InboundActions),
        ("ocpp201", ProtocolVersion.OCPP_201, V201InboundActions),
    ],
)
def test_inbound_requests_and_responses_match_official_schemas(
    conformance_environment,
    version_dir: str,
    protocol: ProtocolVersion,
    resolver_factory,
) -> None:
    schema_root, jsonschema = conformance_environment
    exercise_version(
        schema_root=schema_root,
        jsonschema=jsonschema,
        version_dir=version_dir,
        protocol=protocol,
        resolver_factory=resolver_factory,
    )
