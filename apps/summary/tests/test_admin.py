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
        display="Deterministic Summary",
    )
    suite_feature = Feature.objects.create(
        slug=LLM_SUMMARY_SUITE_FEATURE_SLUG,
        display="Deterministic Summary Suite",
        node_feature=NodeFeature.objects.get(slug="lcd-screen"),
        metadata={
            "parameters": {
                "backend": "ollama",
                "ollama_model": "stale-model",
                "enabled_sources": "logs,state,journal",
            }
        },
    )
    config = LLMSummaryConfig.objects.create()
    admin = LLMSummaryConfigAdmin(LLMSummaryConfig, AdminSite())

    admin._sync_summary_suite_feature(config)

    suite_feature.refresh_from_db()
    assert suite_feature.node_feature == summary_feature
    assert suite_feature.display == "Deterministic Summary Suite"
    parameters = suite_feature.metadata["parameters"]
    assert parameters == {"enabled_sources": "logs,state,journal"}


@pytest.mark.django_db
def test_summary_admin_save_prunes_model_metadata() -> None:
    summary_feature = NodeFeature.objects.create(
        slug="llm-summary",
        display="Deterministic Summary",
    )
    suite_feature = Feature.objects.create(
        slug=LLM_SUMMARY_SUITE_FEATURE_SLUG,
        display="Deterministic Summary Suite",
        node_feature=summary_feature,
        metadata={
            "parameters": {
                "backend": "deterministic",
                "model_path": "models/legacy.gguf",
                "ollama_model": "stale-model",
                "max_source_bytes": "12000",
            }
        },
    )
    config = LLMSummaryConfig.objects.create()
    admin = LLMSummaryConfigAdmin(LLMSummaryConfig, AdminSite())

    admin.save_model(request=None, obj=config, form=None, change=True)

    suite_feature.refresh_from_db()
    assert suite_feature.metadata["parameters"] == {"max_source_bytes": "12000"}
