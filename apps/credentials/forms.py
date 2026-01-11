from django import forms
from django.utils.translation import gettext_lazy as _

from apps.media.models import MediaFile
from apps.media.utils import create_media_file

from .models import SSHAccount, get_ssh_key_bucket


class SSHAccountAdminForm(forms.ModelForm):
    private_key_upload = forms.FileField(required=False, label=_("Private key upload"))
    public_key_upload = forms.FileField(required=False, label=_("Public key upload"))

    class Meta:
        model = SSHAccount
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        bucket = self._get_ssh_key_bucket()
        self.fields["private_key_media"].queryset = MediaFile.objects.filter(bucket=bucket)
        self.fields["public_key_media"].queryset = MediaFile.objects.filter(bucket=bucket)

    def _get_ssh_key_bucket(self):
        if not hasattr(self, "_ssh_key_bucket"):
            self._ssh_key_bucket = get_ssh_key_bucket()
        return self._ssh_key_bucket

    def _clean_key_upload(self, upload):
        if upload:
            bucket = self._get_ssh_key_bucket()
            if not bucket.allows_filename(upload.name):
                raise forms.ValidationError(_("File type is not allowed."))
            if not bucket.allows_size(upload.size):
                raise forms.ValidationError(_("File exceeds the allowed size."))
        return upload

    def save(self, commit=True):
        instance = super().save(commit=False)
        bucket = self._get_ssh_key_bucket()
        private_upload = self.cleaned_data.get("private_key_upload")
        if private_upload:
            instance.private_key_media = create_media_file(bucket=bucket, uploaded_file=private_upload)
        public_upload = self.cleaned_data.get("public_key_upload")
        if public_upload:
            instance.public_key_media = create_media_file(bucket=bucket, uploaded_file=public_upload)
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean_private_key_upload(self):
        upload = self.cleaned_data.get("private_key_upload")
        return self._clean_key_upload(upload)

    def clean_public_key_upload(self):
        upload = self.cleaned_data.get("public_key_upload")
        return self._clean_key_upload(upload)
