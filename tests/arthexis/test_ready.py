from unittest.mock import patch
from urllib.error import URLError

import pytest

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
    with pytest.raises(ValueError, match="requires --local"):
        main()
