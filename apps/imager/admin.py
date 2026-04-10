"""Admin integration for Raspberry Pi image artifacts."""

from ipaddress import ip_address
from pathlib import Path
from socket import getaddrinfo
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.imager.models import RaspberryPiImageArtifact
from apps.imager.services import ImagerBuildError, build_rpi4b_image


class _NoRedirectHandler(HTTPRedirectHandler):
    """Disable automatic redirects so each target can be validated."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _probe_download_url(download_url: str) -> tuple[bool, str]:
    """Check whether an artifact download URL appears reachable."""

    blocked_message = _("Refusing to probe local or private addresses.")
    blocked_flags = ("is_link_local", "is_loopback", "is_multicast", "is_private", "is_unspecified")
    redirect_codes = {301, 302, 303, 307, 308}
    opener = build_opener(_NoRedirectHandler())
    current_url = download_url

    for _redirect_count in range(5):
        parsed_url = urlparse(current_url)
        hostname = parsed_url.hostname
        if parsed_url.scheme not in {"http", "https"} or not hostname:
            return False, _("Unsupported download URL.")
        if hostname == "localhost":
            return False, blocked_message

        try:
            ip_candidate = ip_address(hostname)
        except ValueError:
            ip_candidate = None

        if ip_candidate is not None:
            if any(getattr(ip_candidate, flag) for flag in blocked_flags):
                return False, blocked_message
        else:
            try:
                resolved_hosts = {
                    ip_address(record[4][0]) for record in getaddrinfo(hostname, None, type=0)
                }
            except OSError as exc:
                reason = getattr(exc, "strerror", str(exc))
                return False, str(reason)
            if any(any(getattr(ip_value, flag) for flag in blocked_flags) for ip_value in resolved_hosts):
                return False, blocked_message

        request = Request(current_url, method="HEAD")
        try:
            with opener.open(request, timeout=10) as response:  # noqa: S310 - staff-triggered verification flow
                status = response.getcode()
        except HTTPError as exc:
            location = exc.headers.get("Location")
            if exc.code in redirect_codes and location:
                current_url = urljoin(current_url, location)
                continue
            status = exc.code
        except (URLError, ValueError) as exc:
            reason = getattr(exc, "reason", str(exc))
            return False, str(reason)

        if 200 <= status < 400:
            return True, f"HTTP {status}"
        return False, f"HTTP {status}"

    return False, _("Too many redirects.")


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

    def clean_name(self) -> str:
        """Keep artifact names safe to embed into output filenames."""

        name = self.cleaned_data["name"].strip()
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise ValidationError(_("Artifact name must not contain path separators or traversal segments."))
        return name

    @staticmethod
    def _resolved_within(path: Path, roots: tuple[Path, ...]) -> bool:
        resolved_path = path.resolve(strict=False)
        return any(root == resolved_path or root in resolved_path.parents for root in roots)

    @staticmethod
    def _clean_local_path(raw_path: str, *, allow_file_uri: bool) -> Path:
        parsed = urlparse(raw_path)
        if parsed.scheme in {"http", "https"}:
            raise ValidationError(_("Remote URLs are not valid in this field."))

        if parsed.scheme == "file":
            if not allow_file_uri:
                raise ValidationError(_("File URIs are not valid in this field."))
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                raise ValidationError(_("File URI host must be empty or localhost."))
            local_path = Path(unquote(parsed.path))
        elif parsed.scheme == "":
            local_path = Path(raw_path)
        else:
            raise ValidationError(_("Unsupported path scheme."))

        if not local_path.is_absolute():
            local_path = Path(settings.BASE_DIR) / local_path

        return local_path.resolve(strict=False)

    @staticmethod
    def _base_image_roots() -> tuple[Path, ...]:
        configured_roots = getattr(settings, "IMAGER_ADMIN_BASE_IMAGE_ALLOWED_ROOTS", None)
        if configured_roots:
            return tuple(Path(root).expanduser().resolve(strict=False) for root in configured_roots)

        return (Path(settings.BASE_DIR).resolve(strict=False), Path("/tmp"))

    @staticmethod
    def _output_roots() -> tuple[Path, ...]:
        configured_roots = getattr(settings, "IMAGER_ADMIN_OUTPUT_ALLOWED_ROOTS", None)
        if configured_roots:
            return tuple(Path(root).expanduser().resolve(strict=False) for root in configured_roots)

        return (Path(settings.BASE_DIR).resolve(strict=False),)

    def clean_base_image_uri(self) -> str:
        """Allow remote URIs and constrain local files to configured safe roots."""

        base_image_uri = self.cleaned_data["base_image_uri"].strip()
        parsed = urlparse(base_image_uri)
        if parsed.scheme in {"http", "https"}:
            return base_image_uri

        local_path = self._clean_local_path(base_image_uri, allow_file_uri=True)

        if not self._resolved_within(local_path, self._base_image_roots()):
            raise ValidationError(_("Base image path is outside allowed image directories."))

        return str(local_path)

    def clean_output_dir(self) -> str:
        """Constrain output directories to configured safe roots."""

        output_dir = self.cleaned_data["output_dir"].strip()
        output_path = self._clean_local_path(output_dir, allow_file_uri=False)

        if not self._resolved_within(output_path, self._output_roots()):
            raise ValidationError(_("Output directory is outside allowed output directories."))

        return str(output_path)


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
            ),
            path(
                "create-rpi-image/<int:artifact_id>/test-download/",
                self.admin_site.admin_view(self.test_download_url_view),
                name="imager_raspberrypiimageartifact_test_download_url",
            ),
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
        artifact: RaspberryPiImageArtifact | None = None
        artifact_id = request.GET.get("artifact")
        if artifact_id and artifact_id.isdigit():
            artifact = RaspberryPiImageArtifact.objects.filter(pk=int(artifact_id)).first()

        changelist_url = reverse("admin:imager_raspberrypiimageartifact_changelist")
        if request.method == "POST" and form.is_valid():
            cleaned = form.cleaned_data
            try:
                build_result = build_rpi4b_image(
                    name=cleaned["name"],
                    base_image_uri=cleaned["base_image_uri"],
                    output_dir=Path(cleaned["output_dir"]),
                    download_base_uri=cleaned["download_base_uri"],
                    git_url=cleaned["git_url"],
                    customize=not cleaned["skip_customize"],
                )
            except (ImagerBuildError, OSError) as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("RPI image '%(name)s' was created.") % {"name": cleaned["name"]})
                artifact = RaspberryPiImageArtifact.objects.filter(
                    output_path=str(build_result.output_path),
                ).first()
                if artifact is None:
                    artifact = (
                        RaspberryPiImageArtifact.objects.filter(name=cleaned["name"])
                        .order_by("-created_at")
                        .first()
                    )
                if artifact is not None:
                    return HttpResponseRedirect(
                        f"{reverse('admin:imager_raspberrypiimageartifact_create_rpi_image')}?artifact={artifact.pk}"
                    )
                return HttpResponseRedirect(changelist_url)

        context = {
            **self.admin_site.each_context(request),
            "title": _("Create RPI image"),
            "opts": self.model._meta,
            "form": form,
            "changelist_url": changelist_url,
            "artifact": artifact,
        }
        return TemplateResponse(
            request,
            "admin/imager/raspberrypiimageartifact/create_rpi_image.html",
            context,
        )

    def test_download_url_view(self, request: HttpRequest, artifact_id: int) -> HttpResponse:
        """Probe an artifact download URL from the admin wizard workflow."""

        if not self.has_view_permission(request):
            raise PermissionDenied

        artifact = get_object_or_404(RaspberryPiImageArtifact, pk=artifact_id)
        redirect_url = (
            f"{reverse('admin:imager_raspberrypiimageartifact_create_rpi_image')}"
            f"?artifact={artifact.pk}"
        )
        if not artifact.download_uri:
            messages.warning(request, _("Artifact has no download URL to test."))
            return HttpResponseRedirect(redirect_url)

        reachable, result = _probe_download_url(artifact.download_uri)
        if reachable:
            messages.success(
                request,
                _("Download URL check succeeded for %(name)s (%(result)s).")
                % {"name": artifact.name, "result": result},
            )
        else:
            messages.error(
                request,
                _("Download URL check failed for %(name)s (%(result)s).")
                % {"name": artifact.name, "result": result},
            )

        return HttpResponseRedirect(redirect_url)
