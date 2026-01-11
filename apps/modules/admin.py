from django import forms
from django.contrib import admin
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin
from apps.media.models import MediaFile
from apps.media.utils import create_media_file
from apps.nodes.forms import NodeRoleMultipleChoiceField
from apps.sites.models import Landing

from .models import Module, get_module_favicon_bucket


class LandingInline(admin.TabularInline):
    model = Landing
    extra = 0
    fields = ("path", "label", "enabled", "track_leads", "validation_status", "validated_url_at")
    readonly_fields = ("validation_status", "validated_url_at")
    show_change_link = True


class ModuleAdminForm(forms.ModelForm):
    roles = NodeRoleMultipleChoiceField()
    favicon_upload = forms.ImageField(
        required=False,
        label=_("Favicon upload"),
        help_text=_("Upload a favicon for this module."),
    )

    class Meta:
        model = Module
        fields = (
            "roles",
            "application",
            "path",
            "menu",
            "priority",
            "is_default",
            "favicon_media",
            "favicon_upload",
            "security_group",
            "security_mode",
        )

    class Media:
        css = {"all": ("nodes/css/node_role_multiselect.css",)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        bucket = get_module_favicon_bucket()
        self.fields["favicon_media"].queryset = MediaFile.objects.filter(bucket=bucket)

    def save(self, commit=True):
        instance = super().save(commit=False)
        upload = self.cleaned_data.get("favicon_upload")
        if upload:
            bucket = get_module_favicon_bucket()
            instance.favicon_media = create_media_file(bucket=bucket, uploaded_file=upload)
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean_favicon_upload(self):
        upload = self.cleaned_data.get("favicon_upload")
        if upload:
            bucket = get_module_favicon_bucket()
            if not bucket.allows_filename(upload.name):
                raise forms.ValidationError(_("File type is not allowed."))
            if not bucket.allows_size(upload.size):
                raise forms.ValidationError(_("File exceeds the allowed size."))
        return upload


@admin.register(Module)
class ModuleAdmin(EntityModelAdmin):
    form = ModuleAdminForm
    list_display = (
        "application",
        "roles_display",
        "path",
        "menu",
        "landings_count",
        "priority",
        "is_default",
        "security_group",
        "security_mode",
    )
    list_filter = ("roles", "application", "security_group", "security_mode")
    fields = (
        "roles",
        "application",
        "path",
        "menu",
        "priority",
        "is_default",
        "favicon_media",
        "favicon_upload",
        "favicon_metadata",
        "security_group",
        "security_mode",
    )
    readonly_fields = ("favicon_metadata",)
    inlines = [LandingInline]
    list_select_related = ("application", "security_group")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(landing_count=Count("landings", distinct=True)).prefetch_related("roles")

    @admin.display(description=_("Landings"), ordering="landing_count")
    def landings_count(self, obj):
        return obj.landing_count

    @admin.display(description=_("Roles"))
    def roles_display(self, obj):
        roles = [role.name for role in obj.roles.all()]
        return ", ".join(roles) if roles else _("All")

    @admin.display(description=_("Favicon metadata"))
    def favicon_metadata(self, obj):
        media = getattr(obj, "favicon_media", None)
        if not media:
            return _("No favicon uploaded")
        return _("%(name)s (%(type)s, %(size)s bytes)") % {
            "name": media.original_name or media.file.name,
            "type": media.content_type or _("unknown"),
            "size": media.size,
        }
