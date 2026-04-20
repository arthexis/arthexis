from django import forms
from django.contrib.auth import get_user_model

from apps.groups.models import SecurityGroup

from .models import GalleryCategory, GalleryCredit, GalleryImage, GalleryImageTrait, GalleryTrait


class GalleryUploadForm(forms.Form):
    image = forms.ImageField(
        required=True,
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
    )
    title = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"class": "form-control"}))
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "data-autogrow": "true",
            }
        ),
    )
    include_in_public_gallery = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    create_content_sample = forms.BooleanField(
        required=False,
        label="Also create a Content Sample record",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    owner_user = forms.CharField(required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    owner_group = forms.ModelChoiceField(
        queryset=SecurityGroup.objects.order_by("name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def clean(self):
        cleaned = super().clean()
        owner_user = cleaned.get("owner_user")
        owner_group = cleaned.get("owner_group")
        if bool(owner_user) == bool(owner_group):
            raise forms.ValidationError("Choose exactly one owner user or owner group.")
        return cleaned


class GalleryCategoryForm(forms.ModelForm):
    class Meta:
        model = GalleryCategory
        fields = ("name", "slug", "description")


class GalleryTraitForm(forms.ModelForm):
    class Meta:
        model = GalleryTrait
        fields = ("name", "slug", "description")


class GalleryTraitAssignmentForm(forms.ModelForm):
    class Meta:
        model = GalleryImageTrait
        fields = ("category", "trait", "qualitative_value", "float_value")


class GalleryCreditForm(forms.ModelForm):
    class Meta:
        model = GalleryCredit
        fields = (
            "display_name",
            "artist",
            "series",
            "era",
            "apa_citation",
            "contributed_elements",
            "excluded_elements",
            "link_url",
        )


class GalleryImageForm(forms.ModelForm):
    class Meta:
        model = GalleryImage
        fields = (
            "title",
            "description",
            "include_in_public_gallery",
            "owner_user",
            "owner_group",
            "categories",
        )


class GalleryShareForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": "form-control"}))

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise forms.ValidationError("User not found.")
        return user
