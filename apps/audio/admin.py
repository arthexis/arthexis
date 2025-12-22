from __future__ import annotations

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import NoReverseMatch, path, reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.locals.user_data import EntityModelAdmin
from apps.nodes.models import Node, NodeFeature, NodeFeatureAssignment

from .models import RecordingDevice
from .utils import has_audio_capture_device, record_microphone_sample, save_audio_sample


@admin.register(RecordingDevice)
class RecordingDeviceAdmin(DjangoObjectActions, EntityModelAdmin):
    list_display = ("identifier", "node", "description", "capture_channels")
    search_fields = ("identifier", "description", "raw_info", "node__hostname")
    changelist_actions = ["find_recording_devices"]
    change_list_template = "django_object_actions/change_list.html"

    def get_urls(self):
        custom = [
            path(
                "find-recording-devices/",
                self.admin_site.admin_view(self.find_recording_devices_view),
                name="audio_recordingdevice_find_devices",
            ),
            path(
                "test-microphone/",
                self.admin_site.admin_view(self.test_microphone_view),
                name="audio_recordingdevice_test_microphone",
            ),
        ]
        return custom + super().get_urls()

    def find_recording_devices(self, request, queryset=None):
        return redirect("admin:audio_recordingdevice_find_devices")

    find_recording_devices.label = _("Find Recording Devices")
    find_recording_devices.short_description = _("Find Recording Devices")
    find_recording_devices.changelist = True

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

    def find_recording_devices_view(self, request):
        node = self._get_local_node(request)
        if node is None:
            return redirect("..")

        feature = self._ensure_audio_feature_enabled(
            request,
            _("Find Recording Devices"),
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
            path = record_microphone_sample(duration_seconds=6)
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
