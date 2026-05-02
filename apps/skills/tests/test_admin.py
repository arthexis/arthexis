from __future__ import annotations

import json
import os
import time
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.skills import admin as skills_admin
from apps.skills.models import AgentSkill, AgentSkillFile
from apps.skills.package_services import PACKAGE_FORMAT

pytestmark = [pytest.mark.django_db]


@pytest.fixture(autouse=True)
def isolated_import_storage(monkeypatch, tmp_path):
    storage = FileSystemStorage(location=tmp_path / "media")
    monkeypatch.setattr(skills_admin, "default_storage", storage)
    return storage


def _zip_upload(
    manifest: dict,
    files: dict[str, str | bytes] | None = None,
    *,
    name: str = "codex-skills.zip",
) -> SimpleUploadedFile:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        for archive_path, content in (files or {}).items():
            package.writestr(archive_path, content)
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type="application/zip",
    )


def _raw_zip_upload(
    files: dict[str, str | bytes],
    *,
    name: str = "codex-skills.zip",
) -> SimpleUploadedFile:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as package:
        for archive_path, content in files.items():
            package.writestr(archive_path, content)
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type="application/zip",
    )


def _valid_package_upload(slug: str = "admin-upload") -> SimpleUploadedFile:
    manifest = {
        "format": PACKAGE_FORMAT,
        "skills": [
            {
                "slug": slug,
                "title": "Admin Upload",
                "files": [
                    {"path": "SKILL.md", "included_by_default": True},
                    {
                        "path": "references/rules.md",
                        "included_by_default": True,
                    },
                ],
            }
        ],
    }
    return _zip_upload(
        manifest,
        {
            f"skills/{slug}/SKILL.md": "---\nname: admin-upload\n---\n",
            f"skills/{slug}/references/rules.md": "portable rules",
        },
    )


def test_agent_skill_changelist_links_import_package(admin_client):
    response = admin_client.get(reverse("admin:skills_agentskill_changelist"))

    assert response.status_code == 200
    assert (
        reverse("admin:skills_agentskill_import_package") in response.rendered_content
    )


def test_superuser_can_preview_valid_package_without_db_writes(
    admin_client,
    isolated_import_storage,
):
    response = admin_client.post(
        reverse("admin:skills_agentskill_import_package"),
        {"action": "preview", "package": _valid_package_upload("admin-preview")},
    )

    assert response.status_code == 200
    assert response.context["preview"]["skills"] == [
        {"slug": "admin-preview", "files": 2}
    ]
    assert response.context["preview_token"]
    assert "admin-preview" in response.rendered_content
    session_entry = admin_client.session[skills_admin._SESSION_IMPORT_PACKAGES_KEY][
        response.context["preview_token"]
    ]
    assert "name" in session_entry
    assert isolated_import_storage.exists(session_entry["name"])
    assert not AgentSkill.objects.filter(slug="admin-preview").exists()
    assert not AgentSkillFile.objects.filter(skill__slug="admin-preview").exists()


def test_superuser_can_import_valid_package(admin_client, isolated_import_storage):
    url = reverse("admin:skills_agentskill_import_package")
    preview_response = admin_client.post(
        url,
        {"action": "preview", "package": _valid_package_upload("admin-import")},
    )
    token = preview_response.context["preview_token"]
    session_entry = admin_client.session[skills_admin._SESSION_IMPORT_PACKAGES_KEY][
        token
    ]
    storage_name = session_entry["name"]
    assert isolated_import_storage.exists(storage_name)

    response = admin_client.post(url, {"action": "apply", "token": token}, follow=True)

    assert response.status_code == 200
    assert response.redirect_chain[-1][0] == reverse(
        "admin:skills_agentskill_changelist"
    )
    skill = AgentSkill.objects.get(slug="admin-import")
    assert skill.title == "Admin Upload"
    assert skill.markdown == "---\nname: admin-upload\n---\n"
    assert list(
        skill.package_files.order_by("relative_path").values_list(
            "relative_path",
            "content",
        )
    ) == [
        ("SKILL.md", "---\nname: admin-upload\n---\n"),
        ("references/rules.md", "portable rules"),
    ]
    assert not isolated_import_storage.exists(storage_name)


def test_invalid_package_preview_shows_error_and_writes_nothing(admin_client):
    url = reverse("admin:skills_agentskill_import_package")
    invalid_cases = [
        (
            SimpleUploadedFile(
                "broken.zip",
                b"not a zip",
                content_type="application/zip",
            ),
            "File is not a zip file",
            None,
        ),
        (
            _zip_upload({"format": "wrong", "skills": []}),
            "Unsupported Codex skill package format",
            None,
        ),
        (
            _raw_zip_upload({"skills/missing-manifest/SKILL.md": "demo"}),
            "Missing required manifest.json",
            None,
        ),
        (
            _zip_upload({"format": PACKAGE_FORMAT, "skills": "not a list"}),
            "Package manifest skills must be a list",
            None,
        ),
        (
            _zip_upload({"format": PACKAGE_FORMAT, "skills": ["not an object"]}),
            "Package skill entries must be objects",
            None,
        ),
        (
            _zip_upload(
                {
                    "format": PACKAGE_FORMAT,
                    "skills": [
                        {
                            "slug": "invalid-files",
                            "title": "Invalid Files",
                            "files": "not a list",
                        }
                    ],
                },
            ),
            "Package files must be a list",
            "invalid-files",
        ),
        (
            _zip_upload(
                {
                    "format": PACKAGE_FORMAT,
                    "skills": [
                        {
                            "slug": "unsafe-path",
                            "title": "Unsafe",
                            "files": [
                                {
                                    "path": "../secret.txt",
                                    "included_by_default": True,
                                }
                            ],
                        }
                    ],
                },
            ),
            "Unsafe package path",
            "unsafe-path",
        ),
        (
            _zip_upload(
                {
                    "format": PACKAGE_FORMAT,
                    "skills": [
                        {
                            "slug": "non-string-path",
                            "title": "Non String Path",
                            "files": [
                                {
                                    "path": None,
                                    "included_by_default": True,
                                }
                            ],
                        }
                    ],
                },
            ),
            "Unsafe package path: None",
            "non-string-path",
        ),
    ]

    for upload, error_fragment, slug in invalid_cases:
        skill_count = AgentSkill.objects.count()
        file_count = AgentSkillFile.objects.count()

        response = admin_client.post(url, {"action": "preview", "package": upload})

        assert response.status_code == 200
        assert any(
            error_fragment in str(message) for message in response.context["messages"]
        )
        assert AgentSkill.objects.count() == skill_count
        assert AgentSkillFile.objects.count() == file_count
        if slug:
            assert not AgentSkill.objects.filter(slug=slug).exists()


def test_invalid_package_filename_shows_form_error_and_writes_nothing(admin_client):
    url = reverse("admin:skills_agentskill_import_package")
    skill_count = AgentSkill.objects.count()
    file_count = AgentSkillFile.objects.count()

    response = admin_client.post(
        url,
        {
            "action": "preview",
            "package": SimpleUploadedFile(
                "notazip.txt",
                b"not a zip",
                content_type="application/zip",
            ),
        },
    )

    assert response.status_code == 200
    assert "package" in response.context["form"].errors
    assert "zip" in str(response.context["form"].errors["package"]).lower()
    assert AgentSkill.objects.count() == skill_count
    assert AgentSkillFile.objects.count() == file_count


def test_expired_preview_token_cannot_import_package(
    admin_client,
    isolated_import_storage,
):
    url = reverse("admin:skills_agentskill_import_package")
    preview_response = admin_client.post(
        url,
        {"action": "preview", "package": _valid_package_upload("admin-expired")},
    )
    token = preview_response.context["preview_token"]
    session = admin_client.session
    packages = session[skills_admin._SESSION_IMPORT_PACKAGES_KEY]
    storage_name = packages[token]["name"]
    packages[token]["ts"] = time.time() - skills_admin._IMPORT_PREVIEW_TTL_SECONDS - 5
    session[skills_admin._SESSION_IMPORT_PACKAGES_KEY] = packages
    session.save()

    response = admin_client.post(url, {"action": "apply", "token": token}, follow=True)

    assert response.status_code == 200
    assert not AgentSkill.objects.filter(slug="admin-expired").exists()
    assert not AgentSkillFile.objects.filter(skill__slug="admin-expired").exists()
    assert not isolated_import_storage.exists(storage_name)
    assert not admin_client.session[skills_admin._SESSION_IMPORT_PACKAGES_KEY]


def test_preview_cleans_expired_storage_uploads(
    admin_client,
    isolated_import_storage,
):
    old_upload_name = (
        f"{skills_admin._IMPORT_UPLOAD_STORAGE_DIR}/"
        f"{skills_admin._IMPORT_UPLOAD_PREFIX}old.zip"
    )
    isolated_import_storage.save(old_upload_name, ContentFile(b"stale"))
    old_upload = Path(isolated_import_storage.path(old_upload_name))
    expired_at = time.time() - skills_admin._IMPORT_PREVIEW_TTL_SECONDS - 5
    os.utime(old_upload, (expired_at, expired_at))

    response = admin_client.post(
        reverse("admin:skills_agentskill_import_package"),
        {"action": "preview", "package": _valid_package_upload("admin-cleanup")},
    )

    assert response.status_code == 200
    assert not isolated_import_storage.exists(old_upload_name)
    token = response.context["preview_token"]
    session_entry = admin_client.session[skills_admin._SESSION_IMPORT_PACKAGES_KEY][
        token
    ]
    preview_upload_name = session_entry["name"]
    assert preview_upload_name.startswith(
        f"{skills_admin._IMPORT_UPLOAD_STORAGE_DIR}/"
        f"{skills_admin._IMPORT_UPLOAD_PREFIX}"
    )
    assert isolated_import_storage.exists(preview_upload_name)
    isolated_import_storage.delete(preview_upload_name)


def test_preview_cleanup_handles_use_tz_false(
    admin_client,
    isolated_import_storage,
    settings,
):
    settings.USE_TZ = False
    old_upload_name = (
        f"{skills_admin._IMPORT_UPLOAD_STORAGE_DIR}/"
        f"{skills_admin._IMPORT_UPLOAD_PREFIX}old.zip"
    )
    isolated_import_storage.save(old_upload_name, ContentFile(b"stale"))
    old_upload = Path(isolated_import_storage.path(old_upload_name))
    expired_at = time.time() - skills_admin._IMPORT_PREVIEW_TTL_SECONDS - 5
    os.utime(old_upload, (expired_at, expired_at))

    response = admin_client.post(
        reverse("admin:skills_agentskill_import_package"),
        {"action": "preview", "package": _valid_package_upload("admin-naive")},
    )

    assert response.status_code == 200
    assert not isolated_import_storage.exists(old_upload_name)


def test_import_package_blocks_staff_without_add_and_change_permissions(
    client,
    django_user_model,
):
    user = django_user_model.objects.create_user(
        username="skill-import-limited",
        password="pw",
        is_staff=True,
    )
    client.force_login(user)

    response = client.post(
        reverse("admin:skills_agentskill_import_package"),
        {"action": "preview", "package": _valid_package_upload("blocked-import")},
    )

    assert response.status_code == 403
    assert not AgentSkill.objects.filter(slug="blocked-import").exists()
