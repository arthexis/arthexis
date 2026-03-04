from pathlib import Path

from apps.nginx.maintenance import update_config

DARK_THEME_BACKGROUND_STYLE = "background: #111827;"


def test_update_config_inserts_maintenance_snippets(tmp_path: Path):
    conf = tmp_path / "nginx.conf"
    conf.write_text(
        """
server {
    listen 80;
    server_name example.test;

    location / {
        proxy_pass http://127.0.0.1:8888;
    }
}
""".lstrip(),
        encoding="utf-8",
    )

    result = update_config(conf)

    assert result == 2
    updated = conf.read_text(encoding="utf-8")
    assert updated.startswith("server {")
    assert "error_page 404 /maintenance/404.html;" in updated
    assert "location = /maintenance/index.html" in updated
    assert "location /maintenance/ {" in updated
    assert "proxy_intercept_errors on;" in updated


def test_update_config_skips_non_server_content(tmp_path: Path):
    conf = tmp_path / "nginx.conf"
    conf.write_text(
        """
# Not a server block
location /health {
    return 200 "ok";
}
""".lstrip(),
        encoding="utf-8",
    )

    result = update_config(conf)

    assert result == 0
    assert conf.read_text(encoding="utf-8").startswith("# Not a server block")


def test_app_down_template_uses_dark_background():
    """503 maintenance page should keep a dark outer background and white card."""

    app_down_template = Path("config/data/nginx/maintenance/app-down.html")
    content = app_down_template.read_text(encoding="utf-8")

    assert DARK_THEME_BACKGROUND_STYLE in content
    assert "main {" in content
    assert "background: #ffffff;" in content
    assert re.search(r"color\s*:\s*#111827\s*;", content)
