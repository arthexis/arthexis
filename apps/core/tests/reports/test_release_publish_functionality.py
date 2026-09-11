from types import CodeType, FunctionType

from . import release_publish_regressions as regressions

_CI_CONTRACT_HELPERS = {
    "_publish_workflow_jobs",
    "_workflow_data",
    "_workflow_files",
    "_workflow_on",
    "_workflow_path_triggers",
    "_workflow_step",
    "_walk_values",
}


def _string_constants(code: CodeType) -> set[str]:
    values: set[str] = set()
    for constant in code.co_consts:
        if isinstance(constant, str):
            values.add(constant)
        elif isinstance(constant, CodeType):
            values.update(_string_constants(constant))
    return values


def _is_ci_contract_test(function: FunctionType) -> bool:
    names = set(function.__code__.co_names)
    if names & _CI_CONTRACT_HELPERS:
        return True

    constants = _string_constants(function.__code__)
    reads_repository_workflows = ".github" in constants and "workflows" in constants
    tests_publisher_parser = "_step_confirm_pypi_trusted_publisher_settings" in names
    return reads_repository_workflows and not tests_publisher_parser


for _name, _value in vars(regressions).items():
    if not _name.startswith("test_") or not isinstance(_value, FunctionType):
        continue
    if _is_ci_contract_test(_value):
        continue
    globals()[_name] = _value
