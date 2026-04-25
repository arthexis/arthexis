import importlib

from django.test import Client
from django.urls import reverse

from apps.awg.models import HypergeometricTemplate
from apps.awg.views.reports import _calculate_hypergeometric_totals


hypergeometric_presets_migration = importlib.import_module(
    "apps.awg.migrations.0004_hypergeometric_presets"
)


class MigrationApps:
    def get_model(self, app_label, model_name):
        assert app_label == "awg"
        assert model_name == "HypergeometricTemplate"
        return HypergeometricTemplate


def test_calculator_index_links_to_mtg_hypergeometric_calculator(db):
    response = Client().get(reverse("awg:calculator"))

    assert response.status_code == 200
    body = response.content.decode()
    assert reverse("awg:mtg_hypergeometric") in body
    assert "MTG Hypergeometric" in body


def test_mtg_hypergeometric_calculator_shows_public_presets(db):
    HypergeometricTemplate.objects.create(
        name="Turn-One Sol Ring",
        description="Commander opening hand odds",
        show_in_pages=True,
    )

    response = Client().get(reverse("awg:mtg_hypergeometric"))

    assert response.status_code == 200
    assert "Turn-One Sol Ring" in response.content.decode()


def test_mtg_hypergeometric_calculator_uses_shared_navigation(db):
    response = Client().get(reverse("awg:mtg_hypergeometric"))

    assert response.status_code == 200
    body = response.content.decode()
    assert reverse("awg:calculator") in body
    assert reverse("awg:mtg_hypergeometric") in body
    assert 'aria-current="page" href="/awg/mtg-hypergeometric/"' in body


def test_mtg_hypergeometric_calculator_includes_seeded_format_presets(db):
    hypergeometric_presets_migration.create_presets(MigrationApps(), None)

    response = Client().get(reverse("awg:mtg_hypergeometric"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "40-card Draft" in body
    assert "60-card Standard" in body
    assert "100-card Commander" in body
    assert "360-card Realm" in body


def test_hypergeometric_preset_migration_preserves_duplicate_user_names(db):
    HypergeometricTemplate.objects.create(
        name="40-card Draft",
        description="User draft preset",
        deck_size=40,
        success_states=17,
        draws=7,
        min_successes=2,
        show_in_pages=True,
    )
    HypergeometricTemplate.objects.create(
        name="40-card Draft",
        description="Another user preset with the same display name",
        deck_size=40,
        success_states=16,
        draws=7,
        min_successes=1,
        show_in_pages=True,
    )

    hypergeometric_presets_migration.create_presets(MigrationApps(), None)

    draft_templates = HypergeometricTemplate.objects.filter(name="40-card Draft")
    assert draft_templates.count() == 3
    assert draft_templates.filter(is_seed_data=True).count() == 1
    assert draft_templates.filter(is_seed_data=False).count() == 2
    assert draft_templates.filter(description="User draft preset").exists()
    assert draft_templates.filter(
        description="Another user preset with the same display name"
    ).exists()


def test_hypergeometric_preset_migration_reverse_removes_only_seed_rows(db):
    user_template = HypergeometricTemplate.objects.create(
        name="60-card Standard",
        description="User standard preset",
        deck_size=60,
        success_states=4,
        draws=7,
        min_successes=1,
        show_in_pages=True,
    )
    hypergeometric_presets_migration.create_presets(MigrationApps(), None)

    hypergeometric_presets_migration.remove_presets(MigrationApps(), None)

    assert HypergeometricTemplate.objects.filter(pk=user_template.pk).exists()
    assert not HypergeometricTemplate.objects.filter(is_seed_data=True).exists()


def test_mtg_hypergeometric_calculator_returns_probability_results(db):
    response = Client().post(
        reverse("awg:mtg_hypergeometric"),
        data={
            "deck_size": "60",
            "success_states": "4",
            "draws": "7",
            "min_successes": "1",
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "Chance of at least target amount" in body
    assert "0.3995" in body


def test_mtg_hypergeometric_calculator_rejects_excessive_deck_size(db):
    response = Client().post(
        reverse("awg:mtg_hypergeometric"),
        data={
            "deck_size": "5000",
            "success_states": "4",
            "draws": "7",
            "min_successes": "1",
        },
    )

    assert response.status_code == 200
    assert "Deck size must be 500 or less." in response.content.decode()
