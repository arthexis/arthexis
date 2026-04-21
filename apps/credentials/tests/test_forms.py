import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.credentials.forms import SSHAccountAdminForm
from apps.credentials.models import SSHAccount, get_ssh_key_bucket
from apps.media.models import MediaFile
from apps.nodes.models import Node


@pytest.mark.django_db
def test_ssh_account_form_accepts_existing_media_without_upload():
    node = Node.objects.create(hostname="ssh-form-existing")
    bucket = get_ssh_key_bucket()
    media = MediaFile.objects.create(
        bucket=bucket,
        file=SimpleUploadedFile("id_existing", b"private-key"),
        original_name="id_existing",
        content_type="application/octet-stream",
        size=11,
    )
    form = SSHAccountAdminForm(
        data={
            "node": node.pk,
            "username": "root",
            "password": "",
            "private_key_media": media.pk,
        }
    )

    assert form.is_valid()
    instance = form.save(commit=False)
    assert instance.private_key_media_id == media.pk


@pytest.mark.django_db
def test_ssh_account_form_upload_creates_media_file():
    node = Node.objects.create(hostname="ssh-form-upload")
    form = SSHAccountAdminForm(
        data={"node": node.pk, "username": "root", "password": "secret"},
        files={"private_key_upload": SimpleUploadedFile("id_upload", b"private-key")},
    )

    assert form.is_valid()
    instance = form.save(commit=False)
    assert instance.private_key_media is not None
    assert instance.private_key_media.original_name == "id_upload"


@pytest.mark.django_db
def test_ssh_account_form_setup_bucket_querysets_allows_excluded_media_fields():
    class SSHAccountWithoutMediaFieldsForm(SSHAccountAdminForm):
        class Meta(SSHAccountAdminForm.Meta):
            model = SSHAccount
            exclude = ("private_key_media", "public_key_media")

    node = Node.objects.create(hostname="ssh-form-excluded-fields")
    form = SSHAccountWithoutMediaFieldsForm(
        data={"node": node.pk, "username": "root", "password": "secret"}
    )

    assert "private_key_media" not in form.fields
    assert "public_key_media" not in form.fields
