"""Focused Evergo public-view security regression tests."""

from __future__ import annotations


def test_to_tsv_sanitizes_formula_and_line_break_characters():
    """Security: TSV export must neutralize formulas and sanitize control characters."""

    from apps.evergo.views import _to_tsv

    tsv = _to_tsv(
        [
            {
                "so": "=2+2",
                "customer_name": "Bob\nSmith",
                "status": "+new",
                "full_address": "A\tB",
                "phone": "@phone",
                "charger_brand": "-brand",
                "city": "Monterrey\rNL",
            }
        ]
    )

    assert "'=2+2" in tsv
    assert "Bob Smith" in tsv
    assert "'+new" in tsv
    assert "A B" in tsv
    assert "'@phone" in tsv
    assert "'-brand" in tsv
    assert "Monterrey NL" in tsv
