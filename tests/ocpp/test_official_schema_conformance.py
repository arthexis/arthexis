import os
import unittest
from pathlib import Path

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase
from django.utils import timezone

from apps.ocpp.models import Charger, OcppTransaction
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions as V16InboundActions
from apps.ocpp.protocol.v201.inbound import InboundActions as V201InboundActions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.ocpp.conformance_support import load_schema, minimal_instance


class OfficialSchemaConformanceTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        root = os.environ.get("OCPP_CONFORMANCE_SCHEMA_ROOT")
        if not root:
            raise unittest.SkipTest("official OCPP schemas are not prepared")
        cls.schema_root = Path(root)

        import jsonschema

        cls.jsonschema = jsonschema

    def _schema(self, version: str, action: str, kind: str) -> dict[str, object]:
        return load_schema(
            self.schema_root / version / f"{action}{kind.title()}.json"
        )

    def _validate(self, payload: object, schema: dict[str, object]) -> None:
        self.jsonschema.validate(
            payload,
            schema,
            format_checker=self.jsonschema.FormatChecker(),
        )

    def _seed_business_state(
        self,
        *,
        version: str,
        action: str,
        charger: Charger,
        payload: dict[str, object],
    ) -> None:
        if version == "ocpp16" and action in {"MeterValues", "StopTransaction"}:
            transaction = OcppTransaction.objects.create(
                charger=charger,
                remote_id=f"seed-{action}",
                started_at=timezone.now(),
            )
            payload["transactionId"] = transaction.pk

    def _exercise_version(
        self,
        *,
        version_dir: str,
        protocol: ProtocolVersion,
        resolver_factory,
    ) -> None:
        import json

        index = json.loads(
            (self.schema_root / version_dir / "index.json").read_text(
                encoding="utf-8"
            )
        )
        manifest_name = "ocpp16.json" if version_dir == "ocpp16" else "ocpp201.json"
        manifest = json.loads(
            Path("tests/ocpp/spec", manifest_name).read_text(encoding="utf-8")
        )

        for action in manifest["directions"]["charge_point_to_csms"]:
            with self.subTest(protocol=protocol.value, action=action):
                request_schema = self._schema(version_dir, action, "request")
                response_schema = self._schema(version_dir, action, "response")
                payload = minimal_instance(request_schema)
                self._validate(payload, request_schema)

                charger = Charger.objects.create(
                    identity=f"{version_dir}-{action}",
                    authorization_mode=Charger.AuthorizationMode.OPEN,
                )
                self._seed_business_state(
                    version=version_dir,
                    action=action,
                    charger=charger,
                    payload=payload,
                )
                self._validate(payload, request_schema)

                resolver = resolver_factory(charger)
                dispatcher = FrameDispatcher(
                    version=protocol,
                    pending_calls=PendingCalls(),
                    handler_resolver=resolver.resolve,
                )
                response = async_to_sync(dispatcher.dispatch)(
                    Call(unique_id="conformance", action=action, payload=payload)
                )
                self.assertIsInstance(
                    response,
                    CallResult,
                    msg=f"{protocol.value} {action} rejected a schema-valid request: {response!r}",
                )
                self._validate(response.payload, response_schema)

    def test_ocpp_16_inbound_requests_and_responses_match_official_schemas(self) -> None:
        self._exercise_version(
            version_dir="ocpp16",
            protocol=ProtocolVersion.OCPP_16,
            resolver_factory=V16InboundActions,
        )

    def test_ocpp_201_inbound_requests_and_responses_match_official_schemas(self) -> None:
        self._exercise_version(
            version_dir="ocpp201",
            protocol=ProtocolVersion.OCPP_201,
            resolver_factory=V201InboundActions,
        )
