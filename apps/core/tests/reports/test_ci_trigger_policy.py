from .release_publish_regressions import _workflow_data, _workflow_on


def test_pr_ci_and_install_health_split_pr_and_main_ownership() -> None:
    pr_ci_on = _workflow_on(_workflow_data("ci.yml"))
    install_health_on = _workflow_on(_workflow_data("install-health.yml"))

    assert "pull_request" in pr_ci_on
    assert "push" not in pr_ci_on
    assert "workflow_dispatch" in pr_ci_on

    assert "pull_request" not in install_health_on
    assert install_health_on["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in install_health_on
