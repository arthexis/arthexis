from django import forms

from apps.groups.models import SecurityGroup

from .models import GalleryCategory, GalleryCredit, GalleryImage, GalleryImageTrait, GalleryTrait


class GalleryUploadForm(forms.Form):
    image = forms.ImageField(required=True)
    title = forms.CharField(max_length=255)
    description = forms.CharField(required=False, widget=forms.Textarea)
    include_in_public_gallery = forms.BooleanField(required=False)
    owner_user = forms.CharField(required=False)
    owner_group = forms.ModelChoiceField(queryset=SecurityGroup.objects.order_by("name"), required=False)

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
