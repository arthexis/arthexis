from __future__ import annotations

import pytest
from django.contrib import admin

from apps.features.models import Feature
from apps.nodes.models import NodeFeature
from apps.summary.admin import LLMSummaryConfigAdmin
from apps.summary.constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
from apps.summary.models import LLMSummaryConfig


@pytest.mark.django_db
def test_sync_summary_suite_feature_links_lcd_node_feature() -> None:
    """LLM Summary suite feature should stay linked to the lcd-screen node feature."""

    lcd_feature = NodeFeature.objects.create(slug="lcd-screen", display="LCD Screen")
    config = LLMSummaryConfig.objects.create(
        backend=LLMSummaryConfig.Backend.DETERMINISTIC,
        model_path="/tmp/llm",
    )
    suite_feature = Feature.objects.create(
        slug=LLM_SUMMARY_SUITE_FEATURE_SLUG,
        display="LLM Summary Suite",
        is_enabled=True,
        node_feature=None,
    )

    model_admin = LLMSummaryConfigAdmin(LLMSummaryConfig, admin.site)
    model_admin._sync_summary_suite_feature(config)

    suite_feature.refresh_from_db()
    assert suite_feature.node_feature == lcd_feature
