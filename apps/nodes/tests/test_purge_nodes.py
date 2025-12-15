import pytest
from django.core.management import call_command

from apps.nodes.models import Node


@pytest.mark.django_db
def test_purge_removes_soft_deleted_nodes():
    active = Node.objects.create(hostname="active")
    soft_deleted = Node.all_objects.create(hostname="soft", is_deleted=True)

    call_command("purge_nodes")

    assert Node.objects.filter(pk=active.pk).exists()
    assert not Node.all_objects.filter(pk=soft_deleted.pk).exists()


@pytest.mark.django_db
def test_purge_removes_duplicates_and_keeps_most_recent():
    first = Node.objects.create(hostname="duplicate")
    latest = Node.objects.create(hostname="duplicate")

    call_command("purge_nodes")

    assert Node.objects.filter(pk=latest.pk).exists()
    assert not Node.objects.filter(pk=first.pk).exists()
    assert Node.objects.filter(hostname="duplicate").count() == 1
