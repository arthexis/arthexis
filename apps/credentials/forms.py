from django import forms
from django.utils.translation import gettext_lazy as _

from apps.media.forms_mixins import MediaUploadAdminFormMixin

from .models import SSHAccount, get_ssh_key_bucket


class SSHAccountAdminForm(MediaUploadAdminFormMixin, forms.ModelForm):
    private_key_upload = forms.FileField(required=False, label=_("Private key upload"))
    public_key_upload = forms.FileField(required=False, label=_("Public key upload"))
    media_upload_bindings = {
        "private_key_upload": {
            "media_field": "private_key_media",
            "bucket_provider": get_ssh_key_bucket,
        },
        "public_key_upload": {
            "media_field": "public_key_media",
            "bucket_provider": get_ssh_key_bucket,
        },
    }

    class Meta:
        model = SSHAccount
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_bucket_aware_querysets()

    def save(self, commit=True):
        instance = super().save(commit=False)
        self.store_uploads_on_instance(instance)
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean_private_key_upload(self):
        return self.clean_upload_field("private_key_upload")

    def clean_public_key_upload(self):
        return self.clean_upload_field("public_key_upload")
