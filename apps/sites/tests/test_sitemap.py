import pytest

from apps.modules.models import Module
from apps.sites import views


@pytest.mark.django_db
def test_sitemap_normalizes_module_paths(client, monkeypatch):
    monkeypatch.setattr(views.Node, "get_local", staticmethod(lambda: None))

    Module.objects.create(path="alpha")
    Module.objects.create(path="/beta/")

    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    body = response.content.decode()

    assert Module.objects.filter(path="/alpha/").exists()
    assert "<loc>http://testserver/alpha/</loc>" in body
    assert "<loc>http://testserver/beta/</loc>" in body
    assert body.count("<loc>") == 2
