from .release_publish_regressions import _workflow_data, _workflow_on


def test_linux_ci_and_install_health_split_pr_and_main_ownership() -> None:
    linux_ci_on = _workflow_on(_workflow_data("ci.yml"))
    install_health_on = _workflow_on(_workflow_data("install-health.yml"))

    assert "pull_request" in linux_ci_on
    assert linux_ci_on["push"]["branches"] == ["release/**"]
    assert "main" not in linux_ci_on["push"]["branches"]
    assert "workflow_dispatch" in linux_ci_on

    assert "pull_request" not in install_health_on
    assert install_health_on["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in install_health_on
