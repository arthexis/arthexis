from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

from arthexis.ready import local, main


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        return b'{"status": "ok"}'


def test_local_ready_accepts_healthy_endpoint() -> None:
    with patch("arthexis.ready.urlopen", return_value=Response()) as urlopen:
        assert local(domain="arthexis.com")

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8888/health/"
    assert request.get_header("Host") == "arthexis.com"


def test_local_ready_returns_false_while_service_is_starting() -> None:
    with patch("arthexis.ready.urlopen", side_effect=URLError("not listening")):
        assert local() is False


def test_ready_local_flag_runs_local_check() -> None:
    with patch("arthexis.ready.local", return_value=True) as probe:
        assert main(local=True, domain="arthexis.com")

    probe.assert_called_once_with(
        host="127.0.0.1",
        port=8888,
        domain="arthexis.com",
        path="/health/",
        timeout=2.0,
    )


def test_ready_without_scope_is_not_defined_yet() -> None:
    try:
        main()
    except ValueError as error:
        assert "requires --local" in str(error)
    else:
        raise AssertionError("ready without a scope should fail")


def test_ready_recipe_uses_bounded_repeat_interval() -> None:
    recipe = Path("deploy/ready.rx").read_text(encoding="utf-8")

    assert "ready --local" in recipe
    assert "--until true" in recipe
    assert "--max [readiness_attempts|20]" in recipe
    assert "--interval [readiness_interval|1]" in recipe


def test_watchtower_recipe_composes_readiness() -> None:
    recipe = Path("deploy/watchtower.rx").read_text(encoding="utf-8")

    assert "./ready.rx" in recipe
