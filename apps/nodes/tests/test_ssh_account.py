import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.credentials.models import SSHAccount, get_ssh_key_bucket
from apps.media.models import MediaFile
from apps.nodes.models import Node


@pytest.mark.django_db
def test_ssh_account_requires_credentials():
    node = Node.objects.create(hostname="cred-node")

    account = SSHAccount(node=node, username="admin")

    with pytest.raises(ValidationError):
        account.full_clean()


@pytest.mark.django_db
def test_ssh_account_supports_password_authentication():
    node = Node.objects.create(hostname="password-node")

    account = SSHAccount(node=node, username="admin", password="secret")
    account.full_clean()
    account.save()

    assert SSHAccount.objects.filter(node=node, username="admin").exists()


@pytest.mark.django_db
def test_private_key_upload_path_uses_node_directory(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    node = Node.objects.create(hostname="key-node")

    key_file = SimpleUploadedFile(
        "id_rsa", b"test-key", content_type="application/octet-stream"
    )
    bucket = get_ssh_key_bucket()
    media_file = MediaFile.objects.create(
        bucket=bucket,
        file=key_file,
        original_name=key_file.name,
        content_type=key_file.content_type,
        size=key_file.size,
    )
    account = SSHAccount.objects.create(
        node=node,
        username="root",
        private_key_media=media_file,
    )

    assert account.private_key_media.file.name.startswith(
        f"protocols/buckets/{bucket.slug}/id_rsa"
    )
