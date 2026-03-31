from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.aws.models import AWSCredentials, LightsailInstance
from apps.aws.services import (
    LightsailFetchError,
    create_lightsail_instance,
    delete_lightsail_instance,
    fetch_lightsail_instance,
    parse_instance_details,
)
from apps.locals.user_data import EntityModelAdmin

from .forms import LightsailSetupForm
from .models import DeployInstance, DeployRelease, DeployRun, DeployServer


@admin.register(DeployServer)
class DeployServerAdmin(EntityModelAdmin):
    actions = ["lightsail_setup"]
    changelist_actions = ["lightsail_setup"]
    list_display = (
        "name",
        "provider",
        "region",
        "host",
        "ssh_port",
        "ssh_user",
        "is_enabled",
    )
    list_filter = ("provider", "region", "is_enabled")
    search_fields = ("name", "host", "region")
    autocomplete_fields = ("lightsail_instance",)
    readonly_fields = ("lightsail_user_inputs", "lightsail_discovered_details")

    @admin.display(description=_("Lightsail setup"))
    def lightsail_user_inputs(self, obj):
        if obj is None:
            return _("No linked Lightsail instance.")
        if not obj.lightsail_instance_id:
            return _("No linked Lightsail instance.")
        credentials_name = obj.lightsail_instance.credentials.name if obj.lightsail_instance.credentials else "—"
        return _(
            "Expected user inputs: instance name=%(name)s, region=%(region)s, credentials=%(credentials)s."
        ) % {
            "name": obj.lightsail_instance.name,
            "region": obj.lightsail_instance.region,
            "credentials": credentials_name,
        }

    @admin.display(description=_("Lightsail discovery"))
    def lightsail_discovered_details(self, obj):
        if obj is None:
            return _("No discovered metadata linked yet.")
        if not obj.lightsail_instance_id:
            return _("No discovered metadata linked yet.")
        instance = obj.lightsail_instance
        return _(
            "Discovered from AWS: state=%(state)s, public_ip=%(public_ip)s, private_ip=%(private_ip)s, "
            "zone=%(zone)s, bundle=%(bundle)s."
        ) % {
            "state": instance.state or "—",
            "public_ip": instance.public_ip or "—",
            "private_ip": instance.private_ip or "—",
            "zone": instance.availability_zone or "—",
            "bundle": instance.bundle_id or "—",
        }

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "provider",
                    "region",
                    "host",
                    "ssh_port",
                    "ssh_user",
                    "is_enabled",
                )
            },
        ),
        (
            _("Lightsail setup context"),
            {
                "fields": (
                    "lightsail_instance",
                    "lightsail_user_inputs",
                    "lightsail_discovered_details",
                )
            },
        ),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "lightsail-setup/",
                self.admin_site.admin_view(self.lightsail_setup_view),
                name="deploy_deployserver_lightsail_setup",
            )
        ]
        return custom + urls

    def lightsail_setup(self, request, queryset=None):  # pragma: no cover - admin action
        return HttpResponseRedirect(self._lightsail_setup_url())

    lightsail_setup.label = _("Lightsail setup wizard")
    lightsail_setup.short_description = _("Lightsail setup wizard")
    lightsail_setup.requires_queryset = False

    def _lightsail_setup_url(self):
        return reverse("admin:deploy_deployserver_lightsail_setup")

    def _resolve_credentials(self, form: LightsailSetupForm) -> tuple[AWSCredentials | None, bool]:
        credentials = form.cleaned_data.get("credentials")
        access_key_id = str(form.cleaned_data.get("access_key_id") or "").strip()
        secret_access_key = str(form.cleaned_data.get("secret_access_key") or "").strip()
        created = False
        if credentials is None and access_key_id and secret_access_key:
            credentials, created = AWSCredentials.objects.update_or_create(
                access_key_id=access_key_id,
                defaults={
                    "name": str(form.cleaned_data.get("credential_label") or access_key_id).strip(),
                    "secret_access_key": secret_access_key,
                },
            )
        return credentials, created

    def _validate_credentials_write_permission(self, request: HttpRequest, form: LightsailSetupForm) -> None:
        credentials = form.cleaned_data.get("credentials")
        access_key_id = str(form.cleaned_data.get("access_key_id") or "").strip()
        secret_access_key = str(form.cleaned_data.get("secret_access_key") or "").strip()
        if credentials is not None or not (access_key_id and secret_access_key):
            return
        permission_name = "aws.change_awscredentials"
        if not AWSCredentials.objects.filter(access_key_id=access_key_id).exists():
            permission_name = "aws.add_awscredentials"
        if not request.user.has_perm(permission_name):
            raise PermissionDenied

    def _prepare_instance_details(self, form: LightsailSetupForm, credentials: AWSCredentials | None):
        instance_name = form.cleaned_data["name"].strip()
        region = form.cleaned_data["region"].strip()
        auth_kwargs = {"credentials": credentials}
        if form.cleaned_data["skip_create"]:
            return fetch_lightsail_instance(name=instance_name, region=region, **auth_kwargs)
        return create_lightsail_instance(
            name=instance_name,
            region=region,
            blueprint_id=form.cleaned_data["blueprint_id"].strip(),
            bundle_id=form.cleaned_data["bundle_id"].strip(),
            key_pair_name=str(form.cleaned_data.get("key_pair_name") or "").strip() or None,
            availability_zone=str(form.cleaned_data.get("availability_zone") or "").strip() or None,
            **auth_kwargs,
        )

    def _persist_lightsail_setup(
        self,
        *,
        request: HttpRequest,
        form: LightsailSetupForm,
        details: dict,
        credentials: AWSCredentials | None,
        created_credentials: bool,
        changelist_url: str,
    ) -> HttpResponseRedirect | None:
        instance_name = form.cleaned_data["name"].strip()
        region = form.cleaned_data["region"].strip()
        deploy_instance_name = form.cleaned_data["deploy_instance_name"].strip()
        created_remote = not form.cleaned_data["skip_create"]
        try:
            with transaction.atomic():
                lightsail_defaults = parse_instance_details(details)
                lightsail_defaults["credentials"] = credentials
                lightsail_instance = LightsailInstance.objects.update_or_create(
                    name=instance_name,
                    region=region,
                    defaults=lightsail_defaults,
                )[0]
                deploy_server = DeployServer.objects.update_or_create(
                    name=instance_name,
                    defaults={
                        "provider": DeployServer.Provider.AWS_LIGHTSAIL,
                        "region": region,
                        "host": (details.get("publicIpAddress") or details.get("privateIpAddress") or "").strip(),
                        "ssh_port": form.cleaned_data["ssh_port"],
                        "ssh_user": form.cleaned_data["ssh_user"].strip(),
                        "lightsail_instance": lightsail_instance,
                        "is_enabled": True,
                    },
                )[0]
                deploy_instance = DeployInstance.objects.update_or_create(
                    server=deploy_server,
                    name=deploy_instance_name,
                    defaults={
                        "install_dir": form.cleaned_data["install_dir"],
                        "service_name": form.cleaned_data["service_name"],
                        "env_file": str(form.cleaned_data.get("env_file") or "").strip(),
                        "branch": form.cleaned_data["branch"],
                        "ocpp_port": form.cleaned_data["ocpp_port"],
                        "admin_url": str(form.cleaned_data.get("admin_url") or "").strip(),
                        "is_enabled": True,
                    },
                )[0]
                DeployRun.objects.create(
                    instance=deploy_instance,
                    action=DeployRun.Action.DEPLOY,
                    status=DeployRun.Status.PENDING,
                    requested_by="lightsail_admin_wizard",
                    output="Admin Lightsail setup wizard prepared deployment records.",
                )
        except Exception:
            if created_remote:
                try:
                    delete_lightsail_instance(name=instance_name, region=region, credentials=credentials)
                except LightsailFetchError:
                    pass
            raise
        if created_credentials:
            self.message_user(
                request,
                _("Stored new AWS credentials linked to this Lightsail setup."),
                messages.INFO,
            )
        self.message_user(
            request,
            _("Lightsail deployment records configured for %(name)s (%(region)s).")
            % {"name": instance_name, "region": region},
            messages.SUCCESS,
        )
        return HttpResponseRedirect(changelist_url)

    def lightsail_setup_view(self, request: HttpRequest):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        if request.method == "POST" and not self.has_change_permission(request):
            raise PermissionDenied

        opts = self.model._meta
        changelist_url = reverse("admin:deploy_deployserver_changelist")
        form = LightsailSetupForm(request.POST or None)
        context = {
            **self.admin_site.each_context(request),
            "opts": opts,
            "title": _("Lightsail Setup Wizard"),
            "changelist_url": changelist_url,
            "action_url": self._lightsail_setup_url(),
            "form": form,
        }

        if request.method == "POST" and form.is_valid():
            self._validate_credentials_write_permission(request, form)
            credentials, created_credentials = self._resolve_credentials(form)
            try:
                details = self._prepare_instance_details(form, credentials)
            except LightsailFetchError as exc:
                form.add_error(None, _("Unable to fetch Lightsail instance details: %(error)s") % {"error": exc})
            else:
                if not details:
                    form.add_error(None, _("Lightsail instance details were empty; setup cannot continue."))
                else:
                    host = (details.get("publicIpAddress") or details.get("privateIpAddress") or "").strip()
                    if not host:
                        form.add_error(
                            None,
                            _("Lightsail instance has no public/private IP yet; try again shortly."),
                        )
                    else:
                        redirect = self._persist_lightsail_setup(
                            request=request,
                            form=form,
                            details=details,
                            credentials=credentials,
                            created_credentials=created_credentials,
                            changelist_url=changelist_url,
                        )
                        if redirect is not None:
                            return redirect

        return TemplateResponse(request, "admin/deploy/deployserver/lightsail_setup.html", context)


@admin.register(DeployInstance)
class DeployInstanceAdmin(EntityModelAdmin):
    list_display = (
        "name",
        "server",
        "service_name",
        "install_dir",
        "branch",
        "ocpp_port",
        "is_enabled",
    )
    list_filter = ("server", "branch", "is_enabled")
    search_fields = ("name", "service_name", "install_dir", "env_file")
    autocomplete_fields = ("server",)


@admin.register(DeployRelease)
class DeployReleaseAdmin(EntityModelAdmin):
    list_display = ("version", "git_ref", "image", "created_at")
    search_fields = ("version", "git_ref", "image")


@admin.register(DeployRun)
class DeployRunAdmin(EntityModelAdmin):
    list_display = (
        "instance",
        "action",
        "status",
        "release",
        "requested_by",
        "requested_at",
        "started_at",
        "finished_at",
    )
    list_filter = ("action", "status", "instance__server")
    search_fields = (
        "instance__name",
        "instance__server__name",
        "release__version",
        "requested_by",
    )
    autocomplete_fields = ("instance", "release")
