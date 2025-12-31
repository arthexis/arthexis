import pytest
from django.urls import reverse

from apps.core.views import reports
from apps.release.models import Feature, FeatureTestCase, Package, PackageRelease
from apps.tests.domain import RecordedTestResult, persist_results
from apps.tests.models import TestResult


@pytest.mark.django_db
def test_feature_due_and_persist_results(tmp_path):
    package = Package.objects.create(name="demo-package")
    feature = Feature.objects.create(
        package=package,
        name="CLI Overview",
        slug="cli-overview",
        expected_version="1.0.0",
        summary="Documented CLI entrypoints",
    )
    release = PackageRelease.objects.create(package=package, version="1.0.0")
    log_path = tmp_path / "feature.log"

    result = RecordedTestResult(
        node_id="tests/test_cli.py::test_usage",
        name="test_usage",
        status=TestResult.Status.PASSED,
        duration=0.5,
        log="",
        features=[{"slug": feature.slug, "package": package.name}],
    )
    persist_results([result])

    test_case = FeatureTestCase.objects.get(feature=feature)
    assert test_case.last_status == TestResult.Status.PASSED

    # validation succeeds when linked tests pass
    reports._step_verify_features(release, {}, log_path)


@pytest.mark.django_db
def test_feature_validation_detects_failures(tmp_path):
    package = Package.objects.create(name="demo-package-fail")
    feature = Feature.objects.create(
        package=package,
        name="API parity",
        slug="api-parity",
        expected_version="1.0.0",
    )
    release = PackageRelease.objects.create(package=package, version="1.0.0")
    FeatureTestCase.objects.create(
        feature=feature,
        test_node_id="tests/test_api.py::test_contract",
        test_name="test_contract",
        last_status=TestResult.Status.FAILED,
    )

    with pytest.raises(Exception):
        reports._step_verify_features(release, {}, tmp_path / "feature-fail.log")


@pytest.mark.django_db
def test_feature_index_view(client):
    package = Package.objects.create(name="view-package")
    feature = Feature.objects.create(
        package=package,
        name="UI Tour",
        slug="ui-tour",
        expected_version="2.0.0",
        summary="Walkthrough of primary controls",
        scope="Control nodes → Release module",
    )

    response = client.get(reverse("release-feature-index"))
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert feature.name in content
    assert package.name in content

    detail = client.get(reverse("release-feature-detail", args=[package.name, feature.slug]))
    assert detail.status_code == 200
    assert feature.summary in detail.content.decode("utf-8")
