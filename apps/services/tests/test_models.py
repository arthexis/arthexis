from pathlib import Path

import pytest

from apps.services.models import NodeService


@pytest.mark.django_db
def test_get_template_path_prefers_setting(settings, tmp_path: Path):
    settings.SERVICES_TEMPLATE_DIR = tmp_path
    template_path = tmp_path / "custom.service"
    template_path.write_text("[Service]\nExecStart=/bin/true", encoding="utf-8")

    service = NodeService(
        slug="custom",
        display="Custom",
        unit_template="custom",
        template_path="custom.service",
    )

    resolved = service.get_template_path()
    assert resolved == template_path
    assert service.get_template_body() == template_path.read_text(encoding="utf-8")


@pytest.mark.django_db
def test_compare_to_installed_matches_content(tmp_path: Path):
    base_dir = tmp_path / "base"
    base_dir.mkdir(parents=True)
    (base_dir / ".locks").mkdir(parents=True)
    (base_dir / ".locks" / "service.lck").write_text("demo", encoding="utf-8")

    service_dir = tmp_path / "systemd"
    service_dir.mkdir()

    service = NodeService(
        slug="demo",
        display="Demo",
        unit_template="{service_name}",
        template_content="[Service]\nUser={service_user}\nWorkingDirectory={base_dir}",
    )
    context = service.build_context(base_dir=base_dir)
    expected_body = service.render_template(context)
    (service_dir / "demo.service").write_text(expected_body, encoding="utf-8")

    result = service.compare_to_installed(base_dir=base_dir, service_dir=service_dir)

    assert result["matches"] is True
    assert result["status"] == ""
    assert result["expected"] == expected_body
    assert result["actual"] == expected_body


@pytest.mark.django_db
def test_compare_to_installed_detects_difference(tmp_path: Path):
    base_dir = tmp_path / "base"
    base_dir.mkdir(parents=True)
    (base_dir / ".locks").mkdir(parents=True)
    (base_dir / ".locks" / "service.lck").write_text("demo", encoding="utf-8")

    service_dir = tmp_path / "systemd"
    service_dir.mkdir()
    (service_dir / "demo.service").write_text("[Service]\nUser=someone", encoding="utf-8")

    service = NodeService(
        slug="demo",
        display="Demo",
        unit_template="{service_name}",
        template_content="[Service]\nUser=other",
    )

    result = service.compare_to_installed(base_dir=base_dir, service_dir=service_dir)

    assert result["matches"] is False
    assert "differs" in result["status"]
    assert "other" in result["expected"]
    assert "someone" in result["actual"]
