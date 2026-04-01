import pytest
from django.core.management import call_command

from apps.app.models import Application
from apps.modules.models import Module
from apps.nodes.models import NodeRole
from apps.sites.models import Landing


@pytest.mark.django_db
def test_terminal_modules_fixture_includes_docs_module() -> None:
    for app_name in ["awg", "docs", "ocpp", "shop"]:
        Application.objects.get_or_create(name=app_name)
    NodeRole.objects.get_or_create(name="Terminal")

    call_command(
        "loaddata",
        "apps/sites/fixtures/default__modules_terminal.json",
        verbosity=0,
    )

    docs_module = Module.objects.get(path="/docs/")
    assert docs_module.application.name == "docs"
    assert docs_module.roles.filter(name="Terminal").exists()

    docs_landing = Landing.objects.get(module=docs_module, path="/docs/library/")
    assert docs_landing.label == "Developer Documents"
