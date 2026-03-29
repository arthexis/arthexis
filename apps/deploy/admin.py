from django.contrib import admin

from apps.locals.user_data import EntityModelAdmin

from .models import DeployInstance, DeployRelease, DeployRun, DeployServer


@admin.register(DeployServer)
class DeployServerAdmin(EntityModelAdmin):
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
