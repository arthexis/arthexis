"""Admin integration for Raspberry Pi image artifacts."""

from pathlib import Path

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.imager.models import RaspberryPiImageArtifact
from apps.imager.services import ImagerBuildError, build_rpi4b_image


class RaspberryPiImageBuildForm(forms.Form):
    """Collect operator input for Raspberry Pi image generation from admin UI."""

    name = forms.CharField(max_length=120, help_text=_("Artifact identifier, for example v0-5-0."))
    base_image_uri = forms.CharField(
        max_length=500,
        help_text=_("Base image URI or local path (file://, local path, or https://)."),
    )
    output_dir = forms.CharField(
        max_length=500,
        initial="build/rpi-imager",
        help_text=_("Output directory where the generated image file is saved."),
    )
    download_base_uri = forms.CharField(
        max_length=500,
        required=False,
        help_text=_("Optional host prefix used to compose the download URL."),
    )
    git_url = forms.CharField(
        max_length=500,
        initial="https://github.com/arthexis/arthexis.git",
        help_text=_("Repository cloned on first boot by the generated image."),
    )
    skip_customize = forms.BooleanField(
        required=False,
        help_text=_("Copy the base image without injecting Arthexis bootstrap scripts."),
    )


@admin.register(RaspberryPiImageArtifact)
class RaspberryPiImageArtifactAdmin(DjangoObjectActions, admin.ModelAdmin):
    """Admin settings for generated Raspberry Pi artifacts."""

    change_list_template = "django_object_actions/change_list.html"
    changelist_actions = ("create_rpi_image",)
    dashboard_actions = ("create_rpi_image_dashboard_action",)
    list_display = ("name", "target", "output_filename", "download_uri", "created_at")
    list_filter = ("target", "created_at")
    search_fields = ("name", "target", "output_filename", "download_uri", "base_image_uri")
    readonly_fields = ("sha256", "size_bytes", "created_at", "updated_at")

    def get_urls(self):
        custom_urls = [
            path(
                "create-rpi-image/",
                self.admin_site.admin_view(self.create_rpi_image_view),
                name="imager_raspberrypiimageartifact_create_rpi_image",
            )
        ]
        return custom_urls + super().get_urls()

    def get_changelist_actions(self, request):
        return list(self.changelist_actions)

    def get_dashboard_actions(self, request):
        return list(self.dashboard_actions)

    def create_rpi_image(self, request, queryset=None):
        return HttpResponseRedirect(reverse("admin:imager_raspberrypiimageartifact_create_rpi_image"))

    create_rpi_image.label = _("Create RPI image")
    create_rpi_image.short_description = _("Create RPI image")
    create_rpi_image.changelist = True
    create_rpi_image.requires_queryset = False

    def create_rpi_image_dashboard_action(self, request, queryset=None):
        return self.create_rpi_image(request, queryset)

    create_rpi_image_dashboard_action.label = _("Create RPI image")
    create_rpi_image_dashboard_action.short_description = _("Create RPI image")
    create_rpi_image_dashboard_action.requires_queryset = False
    create_rpi_image_dashboard_action.dashboard_url = "admin:imager_raspberrypiimageartifact_create_rpi_image"

    def create_rpi_image_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_add_permission(request):
            raise PermissionDenied

        form = RaspberryPiImageBuildForm(request.POST or None)
        changelist_url = reverse("admin:imager_raspberrypiimageartifact_changelist")
        if request.method == "POST" and form.is_valid():
            cleaned = form.cleaned_data
            try:
                build_rpi4b_image(
                    name=cleaned["name"],
                    base_image_uri=cleaned["base_image_uri"],
                    output_dir=Path(cleaned["output_dir"]),
                    download_base_uri=cleaned["download_base_uri"],
                    git_url=cleaned["git_url"],
                    customize=not cleaned["skip_customize"],
                )
            except ImagerBuildError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("RPI image '%(name)s' was created.") % {"name": cleaned["name"]})
                return HttpResponseRedirect(changelist_url)

        context = {
            **self.admin_site.each_context(request),
            "title": _("Create RPI image"),
            "opts": self.model._meta,
            "form": form,
            "changelist_url": changelist_url,
        }
        return TemplateResponse(
            request,
            "admin/imager/raspberrypiimageartifact/create_rpi_image.html",
            context,
        )
