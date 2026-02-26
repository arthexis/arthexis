from django.urls import reverse



def test_admindocs_index_links_to_developer_library(admin_client):
    """Admin documentation index should expose the public Developer Library link."""

    response = admin_client.get(reverse("django-admindocs-docroot"))

    assert response.status_code == 200
    assert reverse("docs:docs-library") in response.content.decode()
    assert "Developer Library" in response.content.decode()
