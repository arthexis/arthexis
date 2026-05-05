from pathlib import Path
from unittest import mock
from uuid import uuid4

import pytest
from django.contrib.sites.models import Site

from apps.features.models import Feature
from apps.nodes import utils as nodes_utils
from apps.nodes.feature_detection import node_feature_detection_registry
from apps.nodes.models import Node, NodeFeature
from apps.nodes.models import utils as node_utils
from apps.sites.models import SiteProfile


@pytest.fixture
def isolated_feature_registry():
    node_feature_detection_registry.reset()
    yield
    node_feature_detection_registry.reset()


def test_select_preferred_ip_prefers_global_address():
    addresses = ["192.168.1.10", "8.8.8.8", "10.0.0.5"]

    assert Node._select_preferred_ip(addresses) == "8.8.8.8"


@pytest.mark.parametrize(
    ("slug", "systemctl_command", "lock_file", "expected"),
    [
        pytest.param(
            "rfid-scanner",
            ["systemctl"],
            "rfid-service.lck",
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
    ],
)
def test_detect_auto_feature_systemd_detection_matrix(
    monkeypatch, tmp_path, slug, systemctl_command, lock_file, expected
):
    """Systemd availability should gate lock-file based systemd detections."""

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


@pytest.mark.django_db
def test_refresh_features_skips_auto_enable_when_linked_suite_feature_disabled(
    monkeypatch, tmp_path
):
    """Auto-detected node features should not auto-enable while linked suite features are off."""

    node = Node.objects.create(
        hostname="suite-disabled-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="suite-disabled-node",
        base_path=str(tmp_path),
    )
    feature = NodeFeature.objects.create(slug="gpio-rtc", display="GPIO RTC")
    Feature.objects.create(
        slug="rtc-suite-feature",
        display="RTC Suite Feature",
        is_enabled=False,
        node_feature=feature,
    )
    monkeypatch.setattr("apps.nodes.models.features.has_clock_device", lambda: True)

    node.refresh_features()

    assert not node.features.filter(pk=feature.pk).exists()


@pytest.mark.django_db
@pytest.mark.django_db
def test_refresh_features_skips_lazy_rfid_auto_detection(monkeypatch, tmp_path):
    """Feature refresh should not probe lazy RFID scanner auto-detection."""

    node = Node.objects.create(
        hostname="lazy-rfid-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="lazy-rfid-node",
        base_path=str(tmp_path),
    )
    feature = NodeFeature.objects.create(slug="rfid-scanner", display="RFID Scanner")
    calls: list[str] = []

    def _detect(slug: str, *, base_dir: Path, base_path: Path) -> bool:
        calls.append(slug)
        if slug == "rfid-scanner":
            raise AssertionError("rfid-scanner should be lazily detected")
        return False

    monkeypatch.setattr(node, "_detect_auto_feature", _detect)

    node.refresh_features()

    assert "rfid-scanner" not in calls
    assert not node.features.filter(pk=feature.pk).exists()


@pytest.mark.django_db
def test_refresh_features_reconciles_existing_lazy_rfid_assignment(tmp_path):
    """Feature refresh should clear stale lazy assignments without probing hardware."""

    node = Node.objects.create(
        hostname="lazy-rfid-stale-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="lazy-rfid-stale-node",
        base_path=str(tmp_path),
    )
    feature = NodeFeature.objects.create(slug="rfid-scanner", display="RFID Scanner")
    node.features.add(feature)

    node.refresh_features()

    assert not node.features.filter(pk=feature.pk).exists()


@pytest.mark.django_db
def test_ensure_feature_enabled_lazily_detects_rfid(monkeypatch, tmp_path):
    """On-demand feature checks should detect and assign RFID scanner when needed."""

    node = Node.objects.create(
        hostname="ensure-rfid-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="ensure-rfid-node",
        base_path=str(tmp_path),
    )
    feature = NodeFeature.objects.create(slug="rfid-scanner", display="RFID Scanner")
    calls: list[str] = []

    def _detect(slug: str, *, base_dir: Path, base_path: Path) -> bool:
        calls.append(slug)
        return slug == "rfid-scanner"

    monkeypatch.setattr(node, "_detect_auto_feature", _detect)

    assert nodes_utils.ensure_feature_enabled("rfid-scanner", node=node) is True
    assert calls == ["rfid-scanner"]
    assert node.features.filter(pk=feature.pk).exists()


@pytest.mark.django_db
def test_ensure_feature_enabled_does_not_probe_lazy_feature_on_remote_node(
    monkeypatch, tmp_path
):
    """Remote nodes should not probe local hardware for lazy feature detection."""

    node = Node.objects.create(
        hostname="ensure-rfid-remote-node",
        mac_address=Node.get_current_mac(),
        public_endpoint="ensure-rfid-remote-node",
        base_path=str(tmp_path),
    )
    NodeFeature.objects.create(slug="rfid-scanner", display="RFID Scanner")
    monkeypatch.setattr(Node, "is_local", property(lambda self: False))
    calls: list[str] = []

    def _detect(*args, **kwargs):
        calls.append("called")
        return True

    monkeypatch.setattr(node, "_detect_auto_feature", _detect)

    assert nodes_utils.ensure_feature_enabled("rfid-scanner", node=node) is False
    assert calls == []


@pytest.mark.django_db
def test_ensure_feature_enabled_handles_lazy_detection_exception(monkeypatch, tmp_path):
    """Lazy detection exceptions should be treated as unavailable features."""

    node = Node.objects.create(
        hostname="ensure-rfid-exception-node",
        mac_address=Node.get_current_mac(),
        current_relation=Node.Relation.SELF,
        public_endpoint="ensure-rfid-exception-node",
        base_path=str(tmp_path),
    )
    NodeFeature.objects.create(slug="rfid-scanner", display="RFID Scanner")
    logger = mock.Mock()

    def _raise(*args, **kwargs):
        raise RuntimeError("detector unavailable")

    monkeypatch.setattr(node, "_detect_auto_feature", _raise)

    assert (
        nodes_utils.ensure_feature_enabled("rfid-scanner", node=node, logger=logger)
        is False
    )
    logger.exception.assert_called_once()


@pytest.fixture
def llm_summary_node(tmp_path):
    """Provide a node for llm-summary detection."""

    return (
        Node(
            hostname="summary-node",
            base_path=str(tmp_path),
            public_endpoint="summary-node",
        ),
        tmp_path,
    )


@pytest.mark.django_db
def test_detect_auto_feature_enables_llm_summary_without_lcd_when_config_active(
    llm_summary_node,
):
    """llm-summary detection should reflect generation capability, not LCD output."""

    from apps.summary.models import LLMSummaryConfig

    node, tmp_path = llm_summary_node

    LLMSummaryConfig.objects.create(is_active=True)

    result = node._detect_auto_feature(
        "llm-summary", base_dir=tmp_path, base_path=tmp_path
    )

    assert result is True


@pytest.mark.django_db
def test_detect_auto_feature_skips_llm_summary_when_config_inactive(
    llm_summary_node,
):
    """Inactive summary config should keep the node summary feature off."""

    from apps.summary.models import LLMSummaryConfig

    node, tmp_path = llm_summary_node

    LLMSummaryConfig.objects.create(is_active=False)

    result = node._detect_auto_feature(
        "llm-summary", base_dir=tmp_path, base_path=tmp_path
    )

    assert result is False


@pytest.mark.django_db
def test_detect_auto_feature_does_not_create_llm_summary_config(llm_summary_node):
    """Feature detection should be a read-only probe."""

    from apps.summary.models import LLMSummaryConfig

    node, tmp_path = llm_summary_node

    result = node._detect_auto_feature(
        "llm-summary", base_dir=tmp_path, base_path=tmp_path
    )

    assert result is False
    assert LLMSummaryConfig.objects.count() == 0


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
    domain = f"arthexis-{uuid4().hex}.example"
    site = Site.objects.create(
        domain=domain,
        name="Arthexis",
    )
    SiteProfile.objects.create(site=site, require_https=True)
    node = Node(
        hostname="watchtower",
        base_site=site,
        port=8888,
    )

    urls = list(node.iter_remote_urls("/nodes/info/"))

    assert f"https://{domain}/nodes/info/" in urls
    assert f"https://{domain}:8888/nodes/info/" in urls


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


@pytest.mark.django_db
def test_detect_auto_feature_uses_app_node_feature_hooks(
    monkeypatch, tmp_path, isolated_feature_registry
):
    """Regression: auto-detection should defer to app-provided hook modules."""

    node = Node(
        hostname="hook-node",
        base_path=str(tmp_path),
        public_endpoint="hook-node",
    )

    import apps.cards.node_features as cards_node_features

    setup_calls: list[str] = []

    monkeypatch.setattr(
        cards_node_features,
        "check_node_feature",
        lambda slug, *, node, base_dir, base_path: (
            True if slug == "rfid-scanner" else None
        ),
    )

    def _setup(slug, *, node, base_dir, base_path):
        if slug == "rfid-scanner":
            setup_calls.append(slug)
            return True
        return None

    monkeypatch.setattr(cards_node_features, "setup_node_feature", _setup)

    assert (
        node._detect_auto_feature("rfid-scanner", base_dir=tmp_path, base_path=tmp_path)
        is True
    )
    assert setup_calls == ["rfid-scanner"]


@pytest.mark.django_db
def test_llm_summary_default_action_points_to_configure_wizard() -> None:
    """LLM Summary node feature should expose a Configure admin action."""

    feature = NodeFeature.objects.create(slug="llm-summary", display="LLM Summary")

    action = feature.get_default_action()

    assert action is not None
    assert action.label == "Configure"
    assert action.url_name == "admin:summary_llmsummaryconfig_wizard"
