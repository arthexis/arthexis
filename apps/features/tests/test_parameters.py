from apps.features.parameters import get_feature_parameter_definitions


def test_llm_summary_suite_defines_expected_parameters() -> None:
    definitions = {
        definition.key: definition
        for definition in get_feature_parameter_definitions("llm-summary-suite")
    }

    assert {"min_context_minutes", "max_context_minutes"} <= definitions.keys()
    assert definitions["enabled_sources"].default == "logs,state,journal"
    assert definitions["max_source_bytes"].default == "12000"
