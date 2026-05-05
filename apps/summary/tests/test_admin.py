import pytest
from django.contrib.admin.sites import AdminSite

from apps.features.models import Feature
from apps.nodes.models import NodeFeature
from apps.summary.admin import LLMSummaryConfigAdmin
from apps.summary.constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
from apps.summary.models import LLMSummaryConfig


@pytest.mark.django_db
def test_summary_admin_sync_links_suite_feature_to_summary_node_feature() -> None:
    NodeFeature.objects.create(slug="lcd-screen", display="LCD Screen")
    summary_feature = NodeFeature.objects.create(
        slug="llm-summary",
        display="LLM Summary",
    )
    suite_feature = Feature.objects.create(
        slug=LLM_SUMMARY_SUITE_FEATURE_SLUG,
        display="LLM Summary Suite",
        node_feature=NodeFeature.objects.get(slug="lcd-screen"),
    )
    config = LLMSummaryConfig.objects.create()
    admin = LLMSummaryConfigAdmin(LLMSummaryConfig, AdminSite())

    admin._sync_summary_suite_feature(config)

    suite_feature.refresh_from_db()
    assert suite_feature.node_feature == summary_feature
