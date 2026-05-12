import importlib

from django.test import Client
from django.urls import reverse

from apps.awg.models import HypergeometricTemplate
from apps.awg.views.reports import (
    MAX_HYPERGEOMETRIC_INPUT,
    MTG_PROBABILITY_THRESHOLDS,
    _draws_for_probability_thresholds,
    _mulligan_keep_probability,
    _probability_at_least_successes,
)

hypergeometric_presets_migration = importlib.import_module(
    "apps.awg.migrations.0004_hypergeometric_presets"
)
hypergeometric_description_migration = importlib.import_module(
    "apps.awg.migrations.0005_refresh_hypergeometric_preset_descriptions"
)


class MigrationApps:
    def get_model(self, app_label, model_name):
        assert app_label == "awg"
        assert model_name == "HypergeometricTemplate"
        return HypergeometricTemplate


class MigrationConnection:
    alias = "default"


class MigrationSchemaEditor:
    connection = MigrationConnection()


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
    hypergeometric_presets_migration.create_presets(
        MigrationApps(), MigrationSchemaEditor()
    )

    response = Client().get(reverse("awg:mtg_hypergeometric"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "40-card Draft" in body
    assert "60-card Standard" in body
    assert "100-card Commander" in body
    assert "360-card Realm" in body


def test_hypergeometric_description_migration_keeps_realm_preset(db):
    user_template = HypergeometricTemplate.objects.create(
        name="100-card Commander",
        description="User commander preset",
        deck_size=99,
        success_states=1,
        draws=10,
        min_successes=1,
        show_in_pages=True,
    )
    hypergeometric_presets_migration.create_presets(
        MigrationApps(), MigrationSchemaEditor()
    )

    hypergeometric_description_migration.update_descriptions(
        MigrationApps(), MigrationSchemaEditor()
    )

    user_template.refresh_from_db()
    assert user_template.description == "User commander preset"
    assert HypergeometricTemplate.objects.filter(
        name="100-card Commander",
        description__contains="99-card library after the commander",
        is_seed_data=True,
    ).exists()
    assert HypergeometricTemplate.objects.filter(
        name="360-card Realm",
        description__contains="Realm baseline",
        is_seed_data=True,
    ).exists()


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

    hypergeometric_presets_migration.create_presets(
        MigrationApps(), MigrationSchemaEditor()
    )

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
    hypergeometric_presets_migration.create_presets(
        MigrationApps(), MigrationSchemaEditor()
    )
    HypergeometricTemplate.objects.filter(
        name="60-card Standard",
        is_seed_data=True,
    ).update(description="Admin edited seeded preset")

    hypergeometric_presets_migration.remove_presets(
        MigrationApps(), MigrationSchemaEditor()
    )

    assert HypergeometricTemplate.objects.filter(pk=user_template.pk).exists()
    assert HypergeometricTemplate.objects.filter(
        pk=user_template.pk,
        description="User standard preset",
    ).exists()
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
    assert "Chance of at least selected target amount" in body
    assert "39.95%" in body


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


def test_public_calculators_accessible_without_login(db):
    client = Client()

    assert client.get(reverse("awg:calculator")).status_code == 200
    assert client.get(reverse("awg:electrical_power")).status_code == 200
    assert client.get(reverse("awg:ev_charging")).status_code == 200
    assert client.get(reverse("awg:mtg_hypergeometric")).status_code == 200


def test_ev_charging_calculator_handles_decimal_overflow_input(db):
    response = Client().post(
        reverse("awg:ev_charging"),
        data={
            "battery_kwh": "1e999999999",
            "start_soc": "0",
            "target_soc": "50",
            "charger_power_kw": "1",
            "charging_efficiency": "0.9",
        },
    )

    assert response.status_code == 200
    assert (
        "Unable to calculate EV charging totals for the provided values."
        in response.content.decode()
    )


def test_energy_tariff_requires_login(db):
    response = Client().get(reverse("awg:energy_tariff"))

    assert response.status_code == 302
    assert response["Location"].startswith("/login/?next=")


def test_energy_tariff_is_last_navigation_tab(db):
    response = Client().get(reverse("awg:calculator"))

    assert response.status_code == 200
    body = response.content.decode()
    assert body.rfind("MTG Hypergeometric") < body.rfind("Energy Tariff")


def test_draws_for_probability_thresholds_returns_none_without_targets():
    thresholds = tuple(MTG_PROBABILITY_THRESHOLDS)
    result = _draws_for_probability_thresholds(
        deck_size=60,
        success_states=0,
        thresholds=thresholds,
    )

    assert result == dict.fromkeys(thresholds)


def test_draws_for_probability_thresholds_caps_large_decks():
    result = _draws_for_probability_thresholds(
        deck_size=MAX_HYPERGEOMETRIC_INPUT + 100,
        success_states=1,
        thresholds=(1.0,),
    )

    assert result == {1.0: None}


def test_draws_for_probability_thresholds_respect_selected_minimum():
    any_target = _draws_for_probability_thresholds(
        deck_size=60,
        success_states=4,
        min_successes=1,
        thresholds=(0.8,),
    )
    selected_minimum = _draws_for_probability_thresholds(
        deck_size=60,
        success_states=4,
        min_successes=2,
        thresholds=(0.8,),
    )

    assert any_target[0.8] is not None
    assert selected_minimum[0.8] is not None
    assert any_target[0.8] < selected_minimum[0.8]


def test_mtg_hypergeometric_calculator_derives_cards_seen_on_play(db):
    response = Client().post(
        reverse("awg:mtg_hypergeometric"),
        data={
            "deck_size": "60",
            "success_states": "4",
            "draw_timing": "on_play",
            "draws": "7",
            "turn_number": "3",
            "min_successes": "1",
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "By turn 3 on the play (9 cards seen)" in body
    assert "48.75%" in body


def test_mtg_hypergeometric_opening_hand_ignores_turn_number(db):
    response = Client().post(
        reverse("awg:mtg_hypergeometric"),
        data={
            "deck_size": "60",
            "success_states": "4",
            "draw_timing": "opening_hand",
            "draws": "not-active",
            "turn_number": "",
            "min_successes": "1",
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "Opening hand (7 cards)" in body
    assert "Turn Number is required" not in body


def test_mtg_hypergeometric_ignores_disabled_section_numeric_values(db):
    response = Client().post(
        reverse("awg:mtg_hypergeometric"),
        data={
            "deck_size": "60",
            "success_states": "4",
            "draws": "7",
            "min_successes": "1",
            "mulligan_max": "stale",
            "land_count": "stale",
            "group_a_count": "stale",
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "All inputs must be whole numbers." not in body
    assert "Chance of at least selected target amount" in body


def test_mtg_hypergeometric_calculator_warns_for_commander_copy_limits(db):
    response = Client().post(
        reverse("awg:mtg_hypergeometric"),
        data={
            "mtg_format": "commander",
            "deck_size": "100",
            "success_states": "4",
            "draws": "7",
            "min_successes": "1",
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "Commander odds usually use a 99-card library" in body
    assert "Commander normally allows only one copy" in body


def test_mtg_hypergeometric_calculator_allows_limited_duplicates(db):
    response = Client().post(
        reverse("awg:mtg_hypergeometric"),
        data={
            "mtg_format": "limited",
            "deck_size": "40",
            "success_states": "6",
            "draws": "7",
            "min_successes": "1",
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "normally cannot include more than four" not in body
    assert "Chance of at least selected target amount" in body


def test_mtg_hypergeometric_results_include_draws_to_high_probability(db):
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
    assert "Draws to Probability Thresholds" in body
    assert "Any target" in body
    assert "Selected minimum" in body


def test_mtg_hypergeometric_results_include_london_mulligan_odds(db):
    response = Client().post(
        reverse("awg:mtg_hypergeometric"),
        data={
            "deck_size": "60",
            "success_states": "4",
            "draws": "7",
            "min_successes": "1",
            "mulligan_enabled": "1",
            "mulligan_max": "2",
            "mulligan_condition": "target_lands",
            "land_count": "24",
            "min_lands": "2",
            "max_lands": "5",
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "London Mulligan Odds" in body
    assert "Cumulative Keep Chance" in body
    assert "Final Hand Size" in body


def test_london_mulligan_target_only_draws_seven_before_bottoming():
    opening_keep_odds = _probability_at_least_successes(
        deck_size=60,
        success_states=4,
        draws=7,
        min_successes=1,
    )

    mulligan_to_six_keep_odds = _mulligan_keep_probability(
        deck_size=60,
        success_states=4,
        min_successes=1,
        condition="target",
        final_hand_size=6,
    )

    assert mulligan_to_six_keep_odds == opening_keep_odds


def test_mtg_hypergeometric_results_include_two_package_odds(db):
    response = Client().post(
        reverse("awg:mtg_hypergeometric"),
        data={
            "deck_size": "60",
            "success_states": "4",
            "draws": "7",
            "min_successes": "1",
            "multivariate_enabled": "1",
            "group_a_count": "8",
            "group_a_min": "1",
            "group_b_count": "10",
            "group_b_min": "1",
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "Two-Package Odds" in body
    assert "Chance of seeing both package minimums" in body


def test_mtg_hypergeometric_rejects_impossible_two_package_minimums(db):
    response = Client().post(
        reverse("awg:mtg_hypergeometric"),
        data={
            "deck_size": "60",
            "success_states": "4",
            "draws": "7",
            "min_successes": "1",
            "multivariate_enabled": "1",
            "group_a_count": "8",
            "group_a_min": "4",
            "group_b_count": "10",
            "group_b_min": "4",
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "Two-package minimums cannot exceed cards seen." in body
    assert "Two-Package Odds" not in body


def test_mtg_hypergeometric_results_show_not_reachable_when_no_targets(db):
    response = Client().post(
        reverse("awg:mtg_hypergeometric"),
        data={
            "deck_size": "60",
            "success_states": "0",
            "draws": "7",
            "min_successes": "0",
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert body.count("Not reachable") >= 3
