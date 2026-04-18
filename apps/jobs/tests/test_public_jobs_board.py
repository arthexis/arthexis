from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from django.contrib.auth.models import Group

from apps.groups.constants import PRODUCT_DEVELOPER_GROUP_NAME
from apps.jobs.models import CV_MAX_UPLOAD_BYTES, CVSubmission, JobPosting
from apps.users.models import User


@pytest.mark.django_db
def test_public_jobs_board_lists_only_open_public_postings(client):
    now = timezone.now()
    open_posting = JobPosting.objects.create(
        title="Backend Integration Engineer",
        summary="Build integration workflows for Arthexis.",
        publish_at=now - timedelta(hours=1),
        close_at=now + timedelta(days=3),
        is_public=True,
    )
    JobPosting.objects.create(
        title="Closed Role",
        summary="Not visible anymore.",
        publish_at=now - timedelta(days=2),
        close_at=now - timedelta(hours=1),
        is_public=True,
    )
    JobPosting.objects.create(
        title="Internal Role",
        summary="Hidden from public board.",
        publish_at=now - timedelta(days=1),
        is_public=False,
    )

    response = client.get(reverse("jobs:public-board"))

    assert response.status_code == 200
    content = response.content.decode()
    assert open_posting.title in content
    assert "Closed Role" not in content
    assert "Internal Role" not in content


@pytest.mark.django_db
def test_public_jobs_board_accepts_cv_submission(client):
    posting = JobPosting.objects.create(
        title="Platform Engineer",
        summary="Help evolve Arthexis as an OCPP integration pivot.",
    )

    response = client.post(
        reverse("jobs:public-board"),
        data={
            "job_posting": str(posting.id),
            "full_name": "Jamie Rivera",
            "email": "jamie@example.com",
            "phone": "+1 555 0100",
            "cv_file": SimpleUploadedFile("cv.pdf", b"Experienced engineer", content_type="application/pdf"),
            "cover_letter": "I can help expand Arthexis integrations.",
            "notes": "Available next month.",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert CVSubmission.objects.count() == 1
    submission = CVSubmission.objects.get()
    assert submission.full_name == "Jamie Rivera"
    assert submission.job_posting == posting
    assert submission.is_user_data is True

    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert "Your CV was submitted." in messages[0]


@pytest.mark.django_db
def test_public_jobs_board_rejects_invalid_cv_file_extension(client):
    response = client.post(
        reverse("jobs:public-board"),
        data={
            "full_name": "Casey Jordan",
            "email": "casey@example.com",
            "cv_file": SimpleUploadedFile("cv.exe", b"binary", content_type="application/octet-stream"),
        },
    )

    assert response.status_code == 200
    assert CVSubmission.objects.count() == 0
    assert "File extension" in response.content.decode()


@pytest.mark.django_db
def test_public_jobs_board_rejects_oversized_cv_file(client):
    response = client.post(
        reverse("jobs:public-board"),
        data={
            "full_name": "Casey Jordan",
            "email": "casey@example.com",
            "cv_file": SimpleUploadedFile(
                "cv.pdf",
                b"x" * (CV_MAX_UPLOAD_BYTES + 1),
                content_type="application/pdf",
            ),
        },
    )

    assert response.status_code == 200
    assert CVSubmission.objects.count() == 0
    assert "maximum size" in response.content.decode()


@pytest.mark.django_db
def test_public_jobs_board_rate_limits_submission_requests(client):
    with patch("apps.rates.services.RateLimiter.is_allowed", return_value=False):
        response = client.post(
            reverse("jobs:public-board"),
            data={
                "full_name": "Casey Jordan",
                "email": "casey@example.com",
                "cv_file": SimpleUploadedFile("cv.pdf", b"ok", content_type="application/pdf"),
            },
        )

    assert response.status_code == 429
    assert CVSubmission.objects.count() == 0


@pytest.mark.django_db
def test_developers_library_links_to_jobs_board(client):
    user = User.objects.create_user(username="dev", password="pass123")
    group, _ = Group.objects.get_or_create(name=PRODUCT_DEVELOPER_GROUP_NAME)
    user.groups.add(group)
    client.force_login(user)

    response = client.get(reverse("docs:docs-library"))

    assert response.status_code == 200
    assert reverse("jobs:public-board") in response.content.decode()
