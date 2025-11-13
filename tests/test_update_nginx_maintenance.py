from pathlib import Path

import pytest

from scripts.helpers.update_nginx_maintenance import update_config


pytestmark = [pytest.mark.feature("nginx-server")]


def _write_config(path: Path) -> None:
    path.write_text(
        """
server {
    listen 80;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:9000;
    }
}
""".lstrip()
    )


def test_update_config_injects_custom_404(tmp_path: Path) -> None:
    conf = tmp_path / "nginx.conf"
    _write_config(conf)

    status = update_config(conf)
    content = conf.read_text()

    assert status == 2
    assert "error_page 404 /maintenance/404.html;" in content
    assert "error_page 500 502 503 504 /maintenance/index.html;" in content
    assert "location = /maintenance/404.html" in content
    assert "location = /maintenance/index.html" in content
    assert "proxy_intercept_errors on;" in content


def test_update_config_is_idempotent(tmp_path: Path) -> None:
    conf = tmp_path / "nginx.conf"
    _write_config(conf)

    first_status = update_config(conf)
    second_status = update_config(conf)
    content = conf.read_text()

    assert first_status == 2
    assert second_status == 0
    assert content.count("error_page 404 /maintenance/404.html;") == 1
    assert content.count("error_page 500 502 503 504 /maintenance/index.html;") == 1
