import pytest

from django.contrib.sites.models import Site

from apps.nodes.models import Node, NodeFeature
from apps.nodes.models.core import utils as node_utils


def test_select_preferred_ip_prefers_global_address():
    addresses = ["192.168.1.10", "8.8.8.8", "10.0.0.5"]

    assert Node._select_preferred_ip(addresses) == "8.8.8.8"


@pytest.mark.parametrize(
    ("slug", "systemctl_command", "lock_file", "expected"),
    [
        pytest.param(
            "rfid-scanner",
            ["systemctl"],
            "rfid.lck",
            True,
            id="rfid-lock-requires-systemctl",
        ),
        pytest.param(
            "celery-queue",
            [],
            "celery.lck",
            False,
            id="systemd-lock-blocked-without-systemctl",
        ),
        pytest.param(
            "systemd-manager", ["systemctl"], None, True, id="systemd-manager-detected"
        ),
    ],
)
def test_detect_auto_feature_systemd_detection_matrix(
    monkeypatch, tmp_path, slug, systemctl_command, lock_file, expected
):
    """Systemd-manager availability should gate lock-file based systemd detections."""

    node = Node(
        hostname="auto-feature-node",
        base_path=str(tmp_path),
        public_endpoint="auto-feature",
    )

    if lock_file:
        locks_dir = tmp_path / ".locks"
        locks_dir.mkdir()
        (locks_dir / lock_file).write_text("1")

    monkeypatch.setattr(
        "apps.nodes.models.features._systemctl_command", lambda: systemctl_command
    )

    result = node._detect_auto_feature(slug, base_dir=tmp_path, base_path=tmp_path)

    assert result is expected


def test_detect_auto_feature_delegates_desktop_hooks(monkeypatch, tmp_path):
    """Desktop-related features should delegate detection to desktop node feature hooks."""

    node = Node(
        hostname="desktop-feature-node",
        base_path=str(tmp_path),
        public_endpoint="desktop-feature-node",
    )

    def _check_node_feature(slug: str, *, node):
        del node
        return slug == "systemd-manager"

    monkeypatch.setattr(
        "apps.desktop.node_features.check_node_feature",
        _check_node_feature,
    )

    assert (
        node._detect_auto_feature(
            "systemd-manager", base_dir=tmp_path, base_path=tmp_path
        )
        is True
    )
    assert (
        node._detect_auto_feature("user-desktop", base_dir=tmp_path, base_path=tmp_path)
        is False
    )


def test_detect_auto_feature_allows_rfid_service_probe_without_systemctl(
    monkeypatch, tmp_path
):
    """rfid-scanner fallback service probing should not require systemctl."""

    node = Node(
        hostname="rfid-node",
        base_path=str(tmp_path),
        public_endpoint="rfid-node",
    )

    monkeypatch.setattr("apps.nodes.models.features._systemctl_command", lambda: [])

    import sys
    import types

    stub_rfid_service = types.SimpleNamespace(
        rfid_service_enabled=lambda *, lock_dir: False,
        service_available=lambda: True,
    )
    monkeypatch.setitem(sys.modules, "apps.cards.rfid_service", stub_rfid_service)

    result = node._detect_auto_feature(
        "rfid-scanner", base_dir=tmp_path, base_path=tmp_path
    )

    assert result is True


@pytest.mark.django_db
def test_detect_auto_feature_detects_gpio_rtc_when_clock_device_present(
    monkeypatch, tmp_path
):
    """gpio-rtc auto-detection should defer to the clock-device probe."""

    node = Node(
        hostname="clock-node",
        base_path=str(tmp_path),
        public_endpoint="clock-node",
    )
    monkeypatch.setattr("apps.nodes.models.features.has_clock_device", lambda: True)

    result = node._detect_auto_feature(
        "gpio-rtc", base_dir=tmp_path, base_path=tmp_path
    )

    assert result is True


@pytest.mark.django_db
def test_refresh_features_assigns_gpio_rtc_when_clock_device_present(
    monkeypatch, tmp_path
):
    """Feature refresh should auto-assign gpio-rtc when an RTC is detected."""

    node = Node.objects.create(
        hostname="clock-refresh-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="clock-refresh-node",
        base_path=str(tmp_path),
    )
    feature = NodeFeature.objects.create(slug="gpio-rtc", display="GPIO RTC")
    monkeypatch.setattr("apps.nodes.models.features.has_clock_device", lambda: True)

    node.refresh_features()

    assert node.features.filter(pk=feature.pk).exists()


@pytest.mark.django_db
def test_detect_auto_feature_does_not_detect_gpio_rtc_when_clock_device_absent(
    monkeypatch, tmp_path
):
    """gpio-rtc auto-detection should not trigger when no clock device is present."""

    node = Node(
        hostname="clock-node",
        base_path=str(tmp_path),
        public_endpoint="clock-node",
    )
    monkeypatch.setattr("apps.nodes.models.features.has_clock_device", lambda: False)

    result = node._detect_auto_feature(
        "gpio-rtc", base_dir=tmp_path, base_path=tmp_path
    )

    assert result is False


@pytest.mark.django_db
def test_refresh_features_does_not_assign_gpio_rtc_when_clock_device_absent(
    monkeypatch, tmp_path
):
    """Feature refresh should not auto-assign gpio-rtc when no RTC is detected."""

    node = Node.objects.create(
        hostname="clock-refresh-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="clock-refresh-node",
        base_path=str(tmp_path),
    )
    feature = NodeFeature.objects.create(slug="gpio-rtc", display="GPIO RTC")
    monkeypatch.setattr("apps.nodes.models.features.has_clock_device", lambda: False)

    node.refresh_features()

    assert not node.features.filter(pk=feature.pk).exists()


@pytest.fixture
def llm_summary_node_with_locks(tmp_path):
    """Provide a node with lock files required for llm-summary detection."""
    from apps.screens.startup_notifications import LCD_RUNTIME_LOCK_FILE

    node = Node(
        hostname="summary-node",
        base_path=str(tmp_path),
        public_endpoint="summary-node",
    )

    locks_dir = tmp_path / ".locks"
    locks_dir.mkdir()
    (locks_dir / "celery.lck").write_text("1")
    (locks_dir / LCD_RUNTIME_LOCK_FILE).write_text("1")

    return node, tmp_path


@pytest.mark.django_db
def test_detect_auto_feature_enables_llm_summary_when_prereqs_met(
    llm_summary_node_with_locks,
):
    """llm-summary auto-detection should pass when locks and config are active."""

    from apps.summary.services import get_summary_config

    node, tmp_path = llm_summary_node_with_locks

    config = get_summary_config()
    config.is_active = True
    config.save(update_fields=["is_active", "updated_at"])

    result = node._detect_auto_feature(
        "llm-summary", base_dir=tmp_path, base_path=tmp_path
    )

    assert result is True


@pytest.mark.django_db
def test_detect_auto_feature_disables_llm_summary_when_config_inactive(
    llm_summary_node_with_locks,
):
    """llm-summary auto-detection should fail when config is inactive."""

    from apps.summary.services import get_summary_config

    node, tmp_path = llm_summary_node_with_locks

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


@pytest.mark.django_db
def test_refresh_features_does_not_auto_assign_heavy_feature(tmp_path):
    """Regression: refresh should never auto-enable heavy node features."""

    node = Node.objects.create(
        hostname="heavy-feature-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="heavy-feature-node",
        base_path=str(tmp_path),
    )
    feature = NodeFeature.objects.create(
        slug="celery-queue",
        display="Celery Queue",
        footprint="heavy",
    )

    locks_dir = tmp_path / ".locks"
    locks_dir.mkdir()
    (locks_dir / "celery.lck").write_text("1")

    assert (
        node._detect_auto_feature("celery-queue", base_dir=tmp_path, base_path=tmp_path)
        is True
    )

    node.refresh_features()

    assert not node.features.filter(pk=feature.pk).exists()


def test_format_upgrade_body_handles_missing_release_app(monkeypatch):
    """Regression: formatting should not fail when apps.release is disabled."""

    def _raise_runtime_error(_name: str):
        raise RuntimeError(
            "Model class apps.release.models.package.Package doesn't declare an explicit app_label and isn't in an application in INSTALLED_APPS."
        )

    monkeypatch.setattr(node_utils.importlib, "import_module", _raise_runtime_error)

    assert node_utils._format_upgrade_body("1.2.3", "abcdef1234") == "v1.2.3+ ref1234"


def test_format_upgrade_body_uses_release_matcher_when_available(monkeypatch):
    """Revision suffix should not include plus when release revision matches."""

    class _PackageRelease:
        @staticmethod
        def matches_revision(version: str, revision: str) -> bool:
            return version == "1.2.3" and revision == "abcdef1234"

    class _ReleaseModels:
        PackageRelease = _PackageRelease

    monkeypatch.setattr(
        node_utils.importlib, "import_module", lambda _name: _ReleaseModels
    )

    assert node_utils._format_upgrade_body("1.2.3", "abcdef1234") == "v1.2.3 ref1234"
