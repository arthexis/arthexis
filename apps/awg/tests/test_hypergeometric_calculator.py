from django.test import Client
from django.urls import reverse

from apps.awg.models import HypergeometricTemplate
from apps.awg.views.reports import _calculate_hypergeometric_totals


def test_calculate_hypergeometric_totals_for_common_opening_hand_case():
    result = _calculate_hypergeometric_totals(
        deck_size=60,
        success_states=4,
        draws=7,
        min_successes=1,
        exact_successes=1,
    )

    assert round(result["probability_any"], 4) == 0.3995
    assert round(result["probability_none"], 4) == 0.6005
    assert round(result["probability_exact"], 4) == 0.3363


def test_mtg_hypergeometric_calculator_shows_public_presets(db):
    HypergeometricTemplate.objects.create(
        name="Turn-One Sol Ring",
        description="Commander opening hand odds",
        show_in_pages=True,
    )

    response = Client().get(reverse("awg:mtg_hypergeometric"))

    assert response.status_code == 200
    assert "Turn-One Sol Ring" in response.content.decode()


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
