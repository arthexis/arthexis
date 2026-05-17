from apps.features.parameters import get_feature_parameter_definitions


def test_llm_summary_suite_exposes_context_window_parameters() -> None:
    keys = {
        definition.key
        for definition in get_feature_parameter_definitions("llm-summary-suite")
    }

    assert {"min_context_minutes", "max_context_minutes"} <= keys
