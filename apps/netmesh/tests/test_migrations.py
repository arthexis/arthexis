import importlib

import pytest

from apps.netmesh.models import NodeKeyMaterial
from apps.nodes.models import Node


class _MigrationApps:
    def get_model(self, app_label: str, model_name: str):
        assert app_label == "netmesh"
        assert model_name == "NodeKeyMaterial"
        return NodeKeyMaterial


@pytest.mark.django_db
def test_reclassify_transport_key_types_keeps_x25519_and_corrects_rsa_pem_keys():
    migration = importlib.import_module("apps.netmesh.migrations.0009_reclassify_transport_key_types")
    node = Node.objects.create(hostname="migration-reclassify")

    pem_key = NodeKeyMaterial.objects.create(
        node=node,
        key_type=NodeKeyMaterial.KeyType.X25519,
        key_state=NodeKeyMaterial.KeyState.ACTIVE,
        public_key="-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtest\n-----END PUBLIC KEY-----",
        key_version=1,
    )
    x25519_key = NodeKeyMaterial.objects.create(
        node=node,
        key_type=NodeKeyMaterial.KeyType.RSA_BOOTSTRAP,
        key_state=NodeKeyMaterial.KeyState.RETIRED,
        public_key="x25519:QUJDREVGR0g=",
        key_version=2,
    )

    migration._reclassify_transport_key_types(_MigrationApps(), None)

    pem_key.refresh_from_db()
    x25519_key.refresh_from_db()

    assert pem_key.key_type == NodeKeyMaterial.KeyType.RSA_BOOTSTRAP
    assert x25519_key.key_type == NodeKeyMaterial.KeyType.X25519
