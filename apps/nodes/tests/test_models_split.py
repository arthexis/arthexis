import pytest

from django.contrib.sites.models import Site

from apps.nodes.models import Node


def test_select_preferred_ip_prefers_global_address():
    addresses = ["192.168.1.10", "8.8.8.8", "10.0.0.5"]

    assert Node._select_preferred_ip(addresses) == "8.8.8.8"


def test_detect_auto_feature_uses_lock_file(tmp_path):
    node = Node(
        hostname="auto-feature-node",
        base_path=str(tmp_path),
        public_endpoint="auto-feature",
    )

    locks_dir = tmp_path / ".locks"
    locks_dir.mkdir()
    (locks_dir / "rfid.lck").write_text("1")

    result = node._detect_auto_feature(
        "rfid-scanner", base_dir=tmp_path, base_path=tmp_path
    )

    assert result is True


@pytest.mark.django_db
def test_detect_auto_feature_enables_llm_summary_when_prereqs_met(tmp_path):
    """llm-summary auto-detection should pass when locks and config are active."""

    from apps.summary.services import get_summary_config

    node = Node(
        hostname="summary-node",
        base_path=str(tmp_path),
        public_endpoint="summary-node",
    )

    locks_dir = tmp_path / ".locks"
    locks_dir.mkdir()
    (locks_dir / "celery.lck").write_text("1")
    from apps.screens.startup_notifications import LCD_RUNTIME_LOCK_FILE
    (locks_dir / LCD_RUNTIME_LOCK_FILE).write_text("1")

    config = get_summary_config()
    config.is_active = True
    config.save(update_fields=["is_active", "updated_at"])

    result = node._detect_auto_feature(
        "llm-summary", base_dir=tmp_path, base_path=tmp_path
    )

    assert result is True


@pytest.mark.django_db
def test_detect_auto_feature_disables_llm_summary_when_config_inactive(tmp_path):
    """llm-summary auto-detection should fail when config is inactive."""

    from apps.summary.services import get_summary_config

    node = Node(
        hostname="summary-node",
        base_path=str(tmp_path),
        public_endpoint="summary-node",
    )

    locks_dir = tmp_path / ".locks"
    locks_dir.mkdir()
    (locks_dir / "celery.lck").write_text("1")
    from apps.screens.startup_notifications import LCD_RUNTIME_LOCK_FILE
    (locks_dir / LCD_RUNTIME_LOCK_FILE).write_text("1")

    config = get_summary_config()
    config.is_active = False
    config.save(update_fields=["is_active", "updated_at"])

    result = node._detect_auto_feature(
        "llm-summary", base_dir=tmp_path, base_path=tmp_path
    )

    assert result is False


@pytest.mark.django_db
def test_ensure_keys_generates_keypair(monkeypatch, tmp_path):
    monkeypatch.setattr(Node, "refresh_features", lambda self: None)
    monkeypatch.setattr(Node, "_apply_role_manual_features", lambda self: None)
    node = Node.objects.create(
        hostname="keygen-node",
        public_endpoint="keygen",
        base_path=str(tmp_path),
    )

    node.ensure_keys()

    priv_path = tmp_path / "security" / "keygen"
    pub_path = tmp_path / "security" / "keygen.pub"

    assert priv_path.exists()
    assert pub_path.exists()
    node.refresh_from_db()
    assert node.public_key == pub_path.read_text()


@pytest.mark.django_db
def test_iter_remote_urls_prefers_https_port_443_when_required():
    site = Site.objects.create(
        domain="arthexis.example",
        name="Arthexis",
        require_https=True,
    )
    node = Node(
        hostname="watchtower",
        base_site=site,
        port=8888,
    )

    urls = list(node.iter_remote_urls("/nodes/info/"))

    assert "https://arthexis.example/nodes/info/" in urls
    assert "https://arthexis.example:8888/nodes/info/" in urls
