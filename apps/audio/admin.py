from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment

from .models import AudioSample, RecordingDevice
from .utils import has_audio_capture_device, record_microphone_sample, save_audio_sample


class RecordingDeviceSampleForm(forms.Form):
    device = forms.ModelChoiceField(
        queryset=RecordingDevice.objects.none(),
        required=False,
        empty_label=_("Default device"),
        label=_("Recording device"),
    )

    def __init__(self, *args, node=None, **kwargs):
        super().__init__(*args, **kwargs)
        if node is None:
            return
        queryset = RecordingDevice.objects.filter(node=node).order_by(
            "-is_default", "identifier", "pk"
        )
        self.fields["device"].queryset = queryset
        default_device = queryset.filter(is_default=True).first()
        if default_device:
            self.initial.setdefault("device", default_device)


@admin.register(RecordingDevice)
class RecordingDeviceAdmin(DjangoObjectActions, EntityModelAdmin):
    list_display = (
        "identifier",
        "node",
        "description",
        "capture_channels",
        "is_default",
    )
    search_fields = ("identifier", "description", "raw_info", "node__hostname")
    list_filter = ("node", "is_default")
    changelist_actions = ["find_devices", "take_sample"]
    change_list_template = "django_object_actions/change_list.html"

    def get_urls(self):
        custom = [
            path(
                "find-recording-devices/",
                self.admin_site.admin_view(self.find_devices_view),
                name="audio_recordingdevice_find_devices",
            ),
            path(
                "test-microphone/",
                self.admin_site.admin_view(self.test_microphone_view),
                name="audio_recordingdevice_test_microphone",
            ),
            path(
                "take-sample/",
                self.admin_site.admin_view(self.take_sample_view),
                name="audio_recordingdevice_take_sample",
            ),
        ]
        return custom + super().get_urls()

    def find_devices(self, request, queryset=None):
        return redirect("admin:audio_recordingdevice_find_devices")

    find_devices.label = _("Discover")
    find_devices.short_description = _("Discover")
    find_devices.changelist = True

    def take_sample(self, request, queryset=None):
        return redirect("admin:audio_recordingdevice_take_sample")

    take_sample.label = _("Take Sample")
    take_sample.short_description = _("Take Sample")
    take_sample.changelist = True

    def _ensure_audio_feature_enabled(
        self,
        request,
        action_label: str,
        *,
        node: Node | None = None,
        auto_enable: bool = False,
    ):
        try:
            feature = NodeFeature.objects.get(slug="audio-capture")
        except NodeFeature.DoesNotExist:
            self.message_user(
                request,
                _("%(action)s is unavailable because the feature is not configured.")
                % {"action": action_label},
                level=messages.ERROR,
            )
            return None
        if not feature.is_enabled:
            if auto_enable and node:
                NodeFeatureAssignment.objects.update_or_create(
                    node=node, feature=feature
                )
                node.sync_feature_tasks()
                self.message_user(
                    request,
                    _("%(feature)s feature was automatically enabled.")
                    % {"feature": feature.display},
                    level=messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    _("%(feature)s feature is not enabled on this node.")
                    % {"feature": feature.display},
                    level=messages.WARNING,
                )
                return None
        return feature

    def _get_local_node(self, request):
        node = Node.get_local()
        if node is None:
            self.message_user(
                request,
                _("No local node is registered; cannot perform audio actions."),
                level=messages.ERROR,
            )
        return node

    def find_devices_view(self, request):
        node = self._get_local_node(request)
        if node is None:
            return redirect("..")

        feature = self._ensure_audio_feature_enabled(
            request,
            self.find_devices.label,
            node=node,
            auto_enable=True,
        )
        if not feature:
            return redirect("..")

        if not has_audio_capture_device():
            self.message_user(
                request,
                _("No audio recording devices were detected on this node."),
                level=messages.WARNING,
            )
            return redirect("..")

        created, updated = RecordingDevice.refresh_from_system(node=node)
        if created or updated:
            self.message_user(
                request,
                _("Updated %(created)s new and %(updated)s existing recording devices.")
                % {"created": created, "updated": updated},
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("No recording devices were added or updated."),
                level=messages.INFO,
            )
        return redirect("..")

    def test_microphone_view(self, request):
        feature = self._ensure_audio_feature_enabled(
            request, _("Test Microphone")
        )
        if not feature:
            return redirect("..")

        node = self._get_local_node(request)
        if node is None:
            return redirect("..")

        if not has_audio_capture_device():
            self.message_user(
                request,
                _("Audio Capture feature is enabled but no recording device was detected."),
                level=messages.ERROR,
            )
            return redirect("..")

        try:
            path = record_microphone_sample(duration_seconds=6, node=node)
        except Exception as exc:  # pragma: no cover - depends on system audio
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("..")

        sample = save_audio_sample(path, node=node, method="DEFAULT_ACTION")
        if not sample:
            self.message_user(
                request, _("Duplicate audio sample; not saved"), level=messages.INFO
            )
            return redirect("..")

        self.message_user(
            request, _("Audio sample saved to %(path)s") % {"path": sample.path},
            level=messages.SUCCESS,
        )
        try:
            change_url = reverse(
                "admin:content_contentsample_change", args=[sample.pk]
            )
        except NoReverseMatch:  # pragma: no cover - admin URL always registered
            self.message_user(
                request,
                _("Audio sample saved but the admin page could not be resolved."),
                level=messages.WARNING,
            )
            return redirect("..")
        return redirect(change_url)

    def take_sample_view(self, request):
        feature = self._ensure_audio_feature_enabled(
            request, _("Take Sample"), auto_enable=True
        )
        if not feature:
            return redirect("..")

        node = self._get_local_node(request)
        if node is None:
            return redirect("..")

        if not has_audio_capture_device():
            self.message_user(
                request,
                _("Audio Capture feature is enabled but no recording device was detected."),
                level=messages.ERROR,
            )
            return redirect("..")

        if not RecordingDevice.objects.filter(node=node).exists():
            RecordingDevice.refresh_from_system(node=node)

        devices = RecordingDevice.objects.filter(node=node)
        if not devices.exists():
            self.message_user(
                request,
                _("No recording devices were detected on this node."),
                level=messages.WARNING,
            )
            return redirect("..")

        default_device = RecordingDevice.default_for_node(node) or devices.first()
        audio_sample = None
        sample_url = None

        if request.method == "POST":
            form = RecordingDeviceSampleForm(request.POST, node=node)
            if form.is_valid():
                selected_device = form.cleaned_data.get("device") or default_device
                try:
                    path = record_microphone_sample(
                        duration_seconds=6,
                        device_identifier=(
                            selected_device.identifier if selected_device else None
                        ),
                        node=node,
                    )
                except Exception as exc:  # pragma: no cover - depends on audio stack
                    self.message_user(request, str(exc), level=messages.ERROR)
                else:
                    sample = save_audio_sample(
                        path,
                        node=node,
                        method="ADMIN_SAMPLE",
                        user=request.user,
                        link_duplicates=True,
                    )
                    if not sample:
                        self.message_user(
                            request,
                            _("Duplicate audio sample; not saved"),
                            level=messages.INFO,
                        )
                    else:
                        self.message_user(
                            request,
                            _("Audio sample saved to %(path)s")
                            % {"path": sample.path},
                            level=messages.SUCCESS,
                        )
                        try:
                            sample_url = reverse(
                                "admin:content_contentsample_change",
                                args=[sample.pk],
                            )
                        except NoReverseMatch:  # pragma: no cover - admin URL always registered
                            sample_url = None
                        audio_sample = AudioSample.objects.filter(sample=sample).first()
        else:
            form = RecordingDeviceSampleForm(node=node)

        context = {
            **self.admin_site.each_context(request),
            "title": _("Take Sample"),
            "opts": self.model._meta,
            "form": form,
            "changelist_url": reverse("admin:audio_recordingdevice_changelist"),
            "sample_url": sample_url,
            "sample_audio": audio_sample.get_data_uri() if audio_sample else None,
            "default_device": default_device,
        }
        return TemplateResponse(
            request,
            "admin/audio/recordingdevice/take_sample.html",
            context,
        )
