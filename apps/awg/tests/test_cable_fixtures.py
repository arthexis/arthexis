from pathlib import Path

import pytest
from django.core.management import call_command

from apps.awg.models import CableSize


FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.mark.django_db
def test_cable_size_fixtures_present_and_complete():
    fixtures = sorted(str(path) for path in FIXTURE_DIR.glob("cable_sizes__*.json"))
    assert fixtures, "Expected cable size fixtures to be present"

    call_command("loaddata", *fixtures)

    expected_line_counts = {}
    triple_line_sizes = {"-4", "-3", "-2", "-1"}
    single_line_sizes = {"1", "2", "3", "4", "6", "8", "10", "12", "14"}

    for awg_size in triple_line_sizes:
        expected_line_counts[(awg_size, "cu")] = {1, 2, 3}
        expected_line_counts[(awg_size, "al")] = {1, 2, 3}
    for awg_size in single_line_sizes:
        expected_line_counts[(awg_size, "cu")] = {1}
        expected_line_counts[(awg_size, "al")] = {1}

    qs = CableSize.objects.values("awg_size", "material", "line_num")
    seen_counts = {}
    for item in qs:
        key = (item["awg_size"], item["material"])
        seen_counts.setdefault(key, set()).add(item["line_num"])

    assert CableSize.objects.count() == sum(len(lines) for lines in expected_line_counts.values())
    assert seen_counts == expected_line_counts

    for field in [
        "dia_in",
        "dia_mm",
        "area_kcmil",
        "area_mm2",
        "k_ohm_km",
        "k_ohm_kft",
        "amps_60c",
        "amps_75c",
        "amps_90c",
    ]:
        assert not CableSize.objects.filter(**{f"{field}__lt": 0}).exists()
