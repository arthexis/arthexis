from __future__ import annotations

import datetime as dt

import pytest

from apps.energy.models import ClientReport


@pytest.mark.django_db
def test_build_dataset_returns_zero_totals_without_transactions():
    start = dt.date(2024, 1, 1)
    end = dt.date(2024, 1, 31)

    dataset = ClientReport._build_dataset(start, end)

    assert dataset["schema"] == "evcs-session/v1"
    assert dataset["evcs"] == []
    assert dataset["totals"]["total_kw"] == 0.0
    assert dataset["totals"]["total_kw_period"] == 0.0
