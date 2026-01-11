import json
import os
import subprocess
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _, ngettext

from apps.cards.models import RFID
from apps.cards.sync import serialize_rfid
from apps.core.system import _systemd_unit_status
from apps.ocpp.models import CPForwarder, Charger

from ..models import NetMessage, Node
from apps.content.utils import capture_screenshot, save_screenshot
from .forms import DownloadFirmwareForm, SendNetMessageForm


@admin.action(description="Register Visitor")
def register_visitor(modeladmin, request, queryset):
    return modeladmin.register_visitor_view(request)


@admin.action(description=_("Update selected nodes"))
def update_selected_nodes(modeladmin, request, queryset):
    node_ids = list(queryset.values_list("pk", flat=True))
    if not node_ids:
        modeladmin.message_user(request, _("No nodes selected."), messages.INFO)
        return None
    context = {
        **modeladmin.admin_site.each_context(request),
        "opts": modeladmin.model._meta,
        "title": _("Update selected nodes"),
        "nodes": list(queryset),
        "node_ids": node_ids,
        "progress_url": reverse("admin:nodes_node_update_selected_progress"),
    }
    return TemplateResponse(request, "admin/nodes/node/update_selected.html", context)


@admin.action(description=_("Send Net Message"))
def send_net_message(modeladmin, request, queryset):
    is_submit = "apply" in request.POST
    form = SendNetMessageForm(request.POST if is_submit else None)
    selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
    if not selected_ids:
        selected_ids = [str(pk) for pk in queryset.values_list("pk", flat=True)]
    nodes = []
    cleaned_ids = []
    for value in selected_ids:
        try:
            cleaned_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if cleaned_ids:
        base_queryset = modeladmin.get_queryset(request).filter(pk__in=cleaned_ids)
        nodes_by_pk = {str(node.pk): node for node in base_queryset}
        nodes = [nodes_by_pk[value] for value in selected_ids if value in nodes_by_pk]
    if not nodes:
        nodes = list(queryset)
        selected_ids = [str(node.pk) for node in nodes]
    if not nodes:
        modeladmin.message_user(request, _("No nodes selected."), messages.INFO)
        return None
    if is_submit and form.is_valid():
        subject = form.cleaned_data["subject"]
        body = form.cleaned_data["body"]
        created = 0
        expires_at = form.cleaned_data.get("expires_at")
        for node in nodes:
            message = NetMessage.objects.create(
                subject=subject,
                body=body,
                expires_at=expires_at,
                filter_node=node,
            )
            message.propagate()
            created += 1
        if created:
            success_message = ngettext(
                "Sent %(count)d net message.",
                "Sent %(count)d net messages.",
                created,
            ) % {"count": created}
            modeladmin.message_user(request, success_message, messages.SUCCESS)
        else:
            modeladmin.message_user(
                request, _("No net messages were sent."), messages.INFO
            )
        return None
    context = {
        **modeladmin.admin_site.each_context(request),
        "opts": modeladmin.model._meta,
        "title": _("Send Net Message"),
        "nodes": nodes,
        "selected_ids": selected_ids,
        "action_name": request.POST.get("action", "send_net_message"),
        "select_across": request.POST.get("select_across", "0"),
        "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
        "adminform": helpers.AdminForm(
            form,
            [(None, {"fields": ("subject", "body", "expires_at")})],
            {},
        ),
        "form": form,
        "media": modeladmin.media + form.media,
    }
    return TemplateResponse(request, "admin/nodes/node/send_net_message.html", context)


@admin.action(description=_("Download EVCS firmware"))
def download_evcs_firmware(modeladmin, request, queryset):
    nodes = list(queryset)
    if len(nodes) != 1:
        modeladmin.message_user(
            request,
            _("Select a single node to request firmware."),
            level=messages.ERROR,
        )
        return None
    node = nodes[0]

    if "apply" in request.POST:
        form = DownloadFirmwareForm(node, request.POST)
        if form.is_valid():
            if modeladmin._process_firmware_download(
                request, node, form.cleaned_data
            ):
                return None
    else:
        form = DownloadFirmwareForm(node)

    context = {
        **modeladmin.admin_site.each_context(request),
        "opts": modeladmin.model._meta,
        "title": _("Download EVCS firmware"),
        "node": node,
        "nodes": [node],
        "selected_ids": [str(node.pk)],
        "action_name": request.POST.get("action", "download_evcs_firmware"),
        "select_across": request.POST.get("select_across", "0"),
        "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
        "adminform": helpers.AdminForm(
            form,
            [
                (
                    None,
                    {
                        "fields": (
                            "charger",
                            "vendor_id",
                        )
                    },
                )
            ],
            {},
        ),
        "form": form,
        "media": modeladmin.media + form.media,
    }
    return TemplateResponse(
        request, "admin/nodes/node/download_firmware.html", context
    )


@admin.action(description="Run task")
def run_task(modeladmin, request, queryset):
    if "apply" in request.POST:
        recipe_text = request.POST.get("recipe", "")
        results = []
        for node in queryset:
            try:
                if not node.is_local:
                    raise NotImplementedError(
                        "Remote node execution is not implemented"
                    )
                command = ["/bin/sh", "-c", recipe_text]
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                output = result.stdout + result.stderr
            except Exception as exc:
                output = str(exc)
            results.append((node, output))
        context = {"recipe": recipe_text, "results": results}
        return render(request, "admin/nodes/task_result.html", context)
    context = {"nodes": queryset}
    return render(request, "admin/nodes/node/run_task.html", context)


@admin.action(description="Take Screenshots")
def take_screenshots(modeladmin, request, queryset):
    tx = uuid.uuid4()
    sources = getattr(settings, "SCREENSHOT_SOURCES", ["/"])
    count = 0
    for node in queryset:
        for source in sources:
            try:
                contact_host = node.get_primary_contact()
                url = source.format(
                    node=node, address=contact_host, port=node.port
                )
            except Exception:
                url = source
            if not url.startswith("http"):
                candidate = next(
                    modeladmin._iter_remote_urls(node, url),
                    "",
                )
                if not candidate:
                    modeladmin.message_user(
                        request,
                        _(
                            "No reachable host was available for %(node)s while generating %(path)s"
                        )
                        % {"node": node, "path": url},
                        messages.WARNING,
                    )
                    continue
                url = candidate
            try:
                path = capture_screenshot(url)
            except Exception as exc:  # pragma: no cover - selenium issues
                modeladmin.message_user(request, f"{node}: {exc}", messages.ERROR)
                continue
            sample = save_screenshot(
                path, node=node, method="ADMIN", transaction_uuid=tx
            )
            if sample:
                count += 1
    modeladmin.message_user(request, f"{count} screenshots captured", messages.SUCCESS)


@admin.action(description=_("Import RFIDs from selected"))
def import_rfids_from_selected(modeladmin, request, queryset):
    return modeladmin._run_rfid_import(request, queryset)


@admin.action(description=_("Export RFIDs to selected"))
def export_rfids_to_selected(modeladmin, request, queryset):
    nodes = list(queryset)
    local_node, private_key, error = modeladmin._load_local_node_credentials()
    if error:
        results = [modeladmin._skip_result(node, error) for node in nodes]
        return modeladmin._render_rfid_sync(
            request, "export", results, setup_error=error
        )

    if not nodes:
        return modeladmin._render_rfid_sync(
            request,
            "export",
            [],
            setup_error=_("No nodes selected."),
        )

    rfids = [serialize_rfid(tag) for tag in RFID.objects.all().order_by("label_id")]
    payload = json.dumps(
        {"requester": str(local_node.uuid), "rfids": rfids},
        separators=(",", ":"),
        sort_keys=True,
    )
    signature, error = Node.sign_payload(payload, private_key)
    if error or not signature:
        message = _("Failed to sign payload.")
        if error:
            message = _("Failed to sign payload: %(error)s") % {"error": error}
        return modeladmin._render_rfid_sync(
            request,
            "export",
            [],
            setup_error=message,
        )
    headers = {
        "Content-Type": "application/json",
        "X-Signature": signature,
    }

    results = []
    for node in nodes:
        if local_node.pk and node.pk == local_node.pk:
            results.append(modeladmin._skip_result(node, _("Skipped local node.")))
            continue
        results.append(modeladmin._post_export_to_node(node, payload, headers))

    return modeladmin._render_rfid_sync(request, "export", results)


@admin.action(description=_("Create Charge Point Forwarder"))
def create_charge_point_forwarder(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            _("Select a single remote node."),
            level=messages.ERROR,
        )
        return None

    target = queryset.first()
    local_node = Node.get_local()
    if local_node and target.pk == local_node.pk:
        modeladmin.message_user(
            request,
            _("Cannot create a forwarder targeting the local node."),
            level=messages.ERROR,
        )
        return None

    defaults = {
        "name": target.hostname or str(target),
        "enabled": True,
    }
    if local_node and local_node.pk:
        defaults["source_node"] = local_node

    eligible_qs = Charger.objects.filter(export_transactions=False)
    if local_node and local_node.pk:
        eligible_qs = eligible_qs.filter(
            Q(node_origin=local_node) | Q(node_origin__isnull=True)
        )

    forwarder, created = CPForwarder.objects.get_or_create(
        target_node=target, defaults=defaults
    )

    if created:
        updated = eligible_qs.update(export_transactions=True)
        if updated:
            forwarder.sync_chargers()
            modeladmin.message_user(
                request,
                _("Enabled export transactions for %(count)s charge point(s).")
                % {"count": updated},
                level=messages.INFO,
            )
        modeladmin.message_user(
            request,
            _("Created charge point forwarder for %(node)s.") % {"node": target},
            level=messages.SUCCESS,
        )
    else:
        modeladmin.message_user(
            request,
            _("Forwarder for %(node)s already exists; opening configuration.")
            % {"node": target},
            level=messages.INFO,
        )

    url = reverse("admin:ocpp_cpforwarder_change", args=[forwarder.pk])
    return HttpResponseRedirect(url)


@admin.action(description="Check features for eligibility")
def check_features_for_eligibility(modeladmin, request, queryset):
    from ..feature_checks import feature_checks

    features = list(queryset)
    total = len(features)
    successes = 0
    node = Node.get_local()
    for feature in features:
        enablement_message = modeladmin._manual_enablement_message(feature, node)
        try:
            result = feature_checks.run(feature, node=node)
        except Exception as exc:  # pragma: no cover - defensive
            modeladmin.message_user(
                request,
                f"{feature.display}: {exc} {enablement_message}",
                level=messages.ERROR,
            )
            continue
        if result is None:
            modeladmin.message_user(
                request,
                f"No check is configured for {feature.display}. {enablement_message}",
                level=messages.WARNING,
            )
            continue
        message = result.message or (
            f"{feature.display} check {'passed' if result.success else 'failed'}."
        )
        modeladmin.message_user(
            request, f"{message} {enablement_message}", level=result.level
        )
        if result.success:
            successes += 1
    if total:
        modeladmin.message_user(
            request,
            f"Completed {successes} of {total} feature check(s) successfully.",
            level=messages.INFO,
        )


@admin.action(description="Enable selected action")
def enable_selected_features(modeladmin, request, queryset):
    node = Node.get_local()
    if node is None:
        modeladmin.message_user(
            request,
            "No local node is registered; unable to enable features manually.",
            level=messages.ERROR,
        )
        return None

    manual_features = [
        feature for feature in queryset if feature.slug in Node.MANUAL_FEATURE_SLUGS
    ]
    non_manual_features = [
        feature for feature in queryset if feature.slug not in Node.MANUAL_FEATURE_SLUGS
    ]
    for feature in non_manual_features:
        modeladmin.message_user(
            request,
            f"{feature.display} cannot be enabled manually.",
            level=messages.WARNING,
        )

    if not manual_features:
        modeladmin.message_user(
            request,
            "None of the selected features can be enabled manually.",
            level=messages.WARNING,
        )
        return None

    current_manual = set(
        node.features.filter(slug__in=Node.MANUAL_FEATURE_SLUGS).values_list(
            "slug", flat=True
        )
    )
    desired_manual = current_manual | {feature.slug for feature in manual_features}
    newly_enabled = desired_manual - current_manual
    if not newly_enabled:
        modeladmin.message_user(
            request,
            "Selected manual features are already enabled.",
            level=messages.INFO,
        )
        return None

    node.update_manual_features(desired_manual)
    display_map = {feature.slug: feature.display for feature in manual_features}
    newly_enabled_names = [display_map[slug] for slug in sorted(newly_enabled)]
    modeladmin.message_user(
        request,
        "Enabled {} feature(s): {}".format(
            len(newly_enabled), ", ".join(newly_enabled_names)
        ),
        level=messages.SUCCESS,
    )


@admin.action(description=_("Validate Service Configuration"))
def validate_service_configuration(modeladmin, request, queryset):
    service_dir = Path(os.environ.get("SYSTEMD_DIR", "/etc/systemd/system"))
    base_dir = Path(settings.BASE_DIR)
    for service in queryset:
        result = service.compare_to_installed(
            base_dir=base_dir, service_dir=service_dir
        )
        unit_name = result.get("unit_name") or service.unit_template
        status = result.get("status") or ""
        if result.get("matches"):
            message = _("%(unit)s matches the stored template.") % {"unit": unit_name}
            modeladmin.message_user(request, message, level=messages.SUCCESS)
        else:
            detail = status or _("Installed configuration differs from the template.")
            message = _("%(unit)s: %(detail)s") % {
                "unit": unit_name,
                "detail": detail,
            }
            modeladmin.message_user(request, message, level=messages.WARNING)


@admin.action(description=_("Validate Service is Active"))
def validate_service_active(modeladmin, request, queryset):
    base_dir = Path(settings.BASE_DIR)
    for service in queryset:
        context = service.build_context(base_dir=base_dir)
        unit_name = service.resolve_unit_name(context)
        if not unit_name:
            message = _("Could not resolve a unit name for %(service)s.") % {
                "service": service.display
            }
            modeladmin.message_user(request, message, level=messages.WARNING)
            continue

        status = _systemd_unit_status(unit_name)
        unit_status = status.get("status") or str(_("unknown"))
        enabled_state = status.get("enabled") or ""
        if status.get("missing"):
            message = _("%(unit)s is not installed.") % {"unit": unit_name}
            level = messages.WARNING
        elif unit_status == "active":
            message = _("%(unit)s is active.") % {"unit": unit_name}
            level = messages.SUCCESS
        else:
            detail = enabled_state or unit_status
            message = _("%(unit)s is %(status)s.") % {
                "unit": unit_name,
                "status": detail,
            }
            level = messages.WARNING

        modeladmin.message_user(request, message, level=level)
