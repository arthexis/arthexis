from __future__ import annotations

from django.db import migrations, models
from django.db.migrations.operations.base import Operation
from django.db.models import Q

DEFAULT_BADGE_COLOR = "#28a745"
ROUTER_BADGE_COLOR = "#ff8c00"
ROUTER_NAMES = {"router"}
ROUTER_DOMAINS = {"router", "10.42.0.1"}


class AddSiteDefaultBadgeColor(Operation):
    reduces_to_sql = False
    reversible = True

    def state_forwards(self, app_label, state):
        field = self._get_field()
        state.add_field("sites", "site", field)

    def state_backwards(self, app_label, state):
        state.remove_field("sites", "site", "default_badge_color")

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        to_model = to_state.apps.get_model("sites", "Site")
        from_model = from_state.apps.get_model("sites", "Site")
        field = to_model._meta.get_field("default_badge_color")
        schema_editor.add_field(from_model, field)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        from_model = from_state.apps.get_model("sites", "Site")
        field = from_model._meta.get_field("default_badge_color")
        schema_editor.remove_field(from_model, field)

    def describe(self):
        return "Add default badge color to Site model"

    @staticmethod
    def _get_field():
        field = models.CharField(
            "default badge color",
            max_length=7,
            default=DEFAULT_BADGE_COLOR,
            help_text="Hex color applied when a site lacks an explicit badge override.",
        )
        field.set_attributes_from_name("default_badge_color")
        return field


def _router_filter():
    condition = Q()
    for name in ROUTER_NAMES:
        condition |= Q(name__iexact=name)
    for domain in ROUTER_DOMAINS:
        condition |= Q(domain__iexact=domain)
    return condition


def set_router_default_badge_color(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    Site.objects.filter(
        _router_filter(), default_badge_color=DEFAULT_BADGE_COLOR
    ).update(default_badge_color=ROUTER_BADGE_COLOR)


def revert_router_default_badge_color(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    Site.objects.filter(
        _router_filter(), default_badge_color=ROUTER_BADGE_COLOR
    ).update(default_badge_color=DEFAULT_BADGE_COLOR)


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0007_alter_sitebadge_badge_color"),
        ("sites", "0002_alter_domain_unique"),
    ]

    operations = [
        AddSiteDefaultBadgeColor(),
        migrations.RunPython(
            set_router_default_badge_color, revert_router_default_badge_color
        ),
    ]
