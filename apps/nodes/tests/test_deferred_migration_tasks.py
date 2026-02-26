"""Regression tests for deferred node migration transforms and status endpoint."""

import pytest
from django.urls import reverse

from apps.nodes.models import Node, NodeMigrationCheckpoint, NodeRole
from apps.nodes.tasks import run_deferred_node_migrations


@pytest.mark.django_db
def test_run_deferred_node_migrations_updates_checkpoint_and_data():
    """Deferred migration task should process batched legacy rows and track progress."""

    NodeRole.objects.create(name="Terminal")
    NodeRole.objects.create(name="Control")
    NodeRole.objects.create(name="Unmapped")

    Node.objects.create(hostname="seed-a", is_seed_data=True)
    Node.objects.create(
        hostname="self-a", current_relation=Node.Relation.SELF, address="arthexis.com"
    )
    Node.objects.create(hostname="keep-a")

    first = run_deferred_node_migrations(batch_size=2)
    checkpoint = NodeMigrationCheckpoint.objects.get(key="nodes:legacy-data-cleanup")

    assert first["is_complete"] is False
    assert checkpoint.total_items >= checkpoint.processed_items

    second = run_deferred_node_migrations(batch_size=10)
    checkpoint.refresh_from_db()

    assert second["is_complete"] is True
    assert checkpoint.is_complete is True
    assert Node.objects.filter(hostname="keep-a").exists()
    assert not Node.objects.filter(hostname="seed-a").exists()
    assert not Node.objects.filter(hostname="self-a").exists()
    assert NodeRole.objects.get(name="Terminal").acronym == "TERM"
    assert NodeRole.objects.get(name="Control").acronym == "CTRL"


@pytest.mark.django_db
def test_node_deferred_migration_status_endpoint_returns_percent(client):
    """Status endpoint should expose checkpoint completion for operators."""

    NodeMigrationCheckpoint.objects.create(
        key="nodes:legacy-data-cleanup",
        total_items=8,
        processed_items=2,
    )

    response = client.get(reverse("node-migration-status"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["key"] == "nodes:legacy-data-cleanup"
    assert payload["percent_complete"] == 25.0
    assert payload["is_complete"] is False
    assert "updated_at" in payload
    assert isinstance(payload["updated_at"], str)
    assert payload["updated_at"]
