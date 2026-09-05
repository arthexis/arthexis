from apps.imager.initial_profile import RedirectProfile


def test_redirect_rules_include_observability_counter():
    redirect = RedirectProfile(
        interface="eth0",
        charger_ip="192.168.129.184",
        targets=("3.68.180.92", "160.161.34.164"),
        target_port=80,
        listen_port=8888,
        table="arthexis_ocpp_redirect",
    )

    ruleset = redirect.ruleset()

    assert "tcp dport 80 counter redirect to :8888" in ruleset
