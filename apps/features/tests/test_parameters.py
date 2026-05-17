from apps.features.parameters import get_feature_parameter_definitions


def test_llm_summary_suite_defines_source_registry_parameters() -> None:
    definitions = {
        definition.key: definition
        for definition in get_feature_parameter_definitions("llm-summary-suite")
    }

    assert definitions["enabled_sources"].default == "logs,state,journal"
    assert definitions["max_source_bytes"].default == "12000"
