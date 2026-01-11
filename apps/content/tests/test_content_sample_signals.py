import pytest

from apps.content.models import ContentSample
from apps.content import signals as content_signals
from apps.nodes.models import Node


@pytest.mark.django_db
def test_content_sample_defaults_to_local_node(monkeypatch):
    local_node = Node.objects.create(hostname="local-node")
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: local_node))
    monkeypatch.setattr(content_signals, "should_skip_default_classifiers", lambda: True)

    sample = ContentSample.objects.create(kind=ContentSample.TEXT)

    assert sample.node == local_node
