import io
from urllib.error import URLError
from unittest.mock import patch

from deploy.ready import ready


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        return b'{"status": "ok"}'


def test_ready_accepts_healthy_local_endpoint() -> None:
    with patch("deploy.ready.urlopen", return_value=Response()) as urlopen:
        assert ready(domain="arthexis.com")

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8888/health/"
    assert request.get_header("Host") == "arthexis.com"


def test_ready_returns_false_while_service_is_starting() -> None:
    with patch("deploy.ready.urlopen", side_effect=URLError("not listening")):
        assert ready() is False


def test_ready_recipe_uses_bounded_repeat_interval() -> None:
    recipe = __import__("pathlib").Path("deploy/ready.rx").read_text(encoding="utf-8")

    assert "repeat" in recipe
    assert "--until true" in recipe
    assert "--max [readiness_attempts|20]" in recipe
    assert "--interval [readiness_interval|1]" in recipe


def test_watchtower_recipe_composes_readiness() -> None:
    recipe = __import__("pathlib").Path("deploy/watchtower.rx").read_text(encoding="utf-8")

    assert "./ready.rx" in recipe
