import pytest
from django.urls import reverse

from apps.nginx.models import SiteConfiguration
from apps.nginx.renderers import generate_primary_config


@pytest.mark.django_db
def test_preview_view_shows_file_status(admin_client, tmp_path):
    staging = tmp_path / "sites.json"
    site_destination = tmp_path / "sites.conf"
    primary_path = tmp_path / "arthexis.conf"

    config = SiteConfiguration.objects.create(
        name="preview",
        expected_path=str(primary_path),
        site_entries_path=str(staging),
        site_destination=str(site_destination),
        port=8080,
    )

    staging.write_text('[{"domain": "test.example.com", "require_https": false}]', encoding="utf-8")

    primary_content = generate_primary_config(config.mode, config.port, include_ipv6=config.include_ipv6)
    primary_path.write_text(primary_content, encoding="utf-8")

    url = reverse("admin:nginx_siteconfiguration_preview") + f"?ids={config.pk}"
    response = admin_client.get(url)

    assert response.status_code == 200
    rendered = response.content.decode()
    assert str(config.expected_destination) in rendered
    assert str(config.site_destination_path) in rendered
    assert "Existing file already matches this content." in rendered
    assert "File does not exist on disk." in rendered


@pytest.mark.django_db
def test_preview_view_denies_user_without_permission(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="staff-no-view", email="staff@example.com", password="secret"
    )
    user.is_staff = True
    user.save()

    client.force_login(user)

    url = reverse("admin:nginx_siteconfiguration_preview")
    response = client.get(url)

    assert response.status_code == 403
