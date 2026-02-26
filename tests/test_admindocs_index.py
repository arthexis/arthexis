from django.urls import reverse


def test_admindocs_index_links_to_developer_library(admin_client):
    """Admin documentation index should expose the public Developer Library link."""

    response = admin_client.get(reverse("django-admindocs-docroot"))

    assert response.status_code == 200
    content = response.content.decode()
    expected_link = f'<a href="{reverse("docs:docs-library")}">Developer Library</a>'
    assert expected_link in content
