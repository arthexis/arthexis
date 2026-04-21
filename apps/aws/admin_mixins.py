from __future__ import annotations

from collections.abc import Callable

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.discovery.services import record_discovery_item, start_discovery

from .services import LightsailFetchError


class LightsailFetchAdminMixin:
    """Shared fetch-tool wiring for Lightsail-backed admin views."""

    fetch_action_label = _("Discover")
    fetch_permission_method = "has_change_permission"
    fetch_route_name: str
    fetch_template_name: str
    fetch_title: str
    fetch_form_class: type
    fetch_service: Callable
    fetch_parse_details: Callable
    fetch_update_or_create_target: Callable
    fetch_discovery_action: str
    fetch_success_noun: str

    def get_urls(self):  # pragma: no cover - admin hook
        urls = super().get_urls()
        custom = [
            path(
                "fetch/",
                self.admin_site.admin_view(self.fetch_view),
                name=self.fetch_route_name,
            ),
        ]
        return custom + urls

    def _action_url(self):
        return reverse(f"admin:{self.fetch_route_name}")

    def fetch(self, request, queryset=None):  # pragma: no cover - admin action
        return HttpResponseRedirect(self._action_url())

    fetch.label = fetch_action_label
    fetch.short_description = fetch_action_label
    fetch.requires_queryset = False
    fetch.is_discover_action = True

    def _check_fetch_permission(self, request: HttpRequest) -> None:
        permission_check = getattr(self, self.fetch_permission_method)
        if not permission_check(request):
            raise PermissionDenied

    def _fetch_template_context(self, request: HttpRequest, form):
        return {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": self.fetch_title,
            "changelist_url": reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist"),
            "action_url": self._action_url(),
            "form": form,
        }

    def _fetch_service_kwargs(self, form, credentials):
        return {
            "name": form.cleaned_data["name"],
            "region": form.cleaned_data["region"],
            "credentials": credentials,
            "access_key_id": form.cleaned_data.get("access_key_id"),
            "secret_access_key": form.cleaned_data.get("secret_access_key"),
        }

    def _fetch_lookup_kwargs(self, form):
        return {
            "name": form.cleaned_data["name"],
            "region": form.cleaned_data["region"],
        }

    def _apply_fetch_defaults(self, defaults, form, credentials):
        defaults.update({"region": form.cleaned_data["region"], "credentials": credentials})
        return defaults

    def _fetch_success_messages(self, request, *, obj, created: bool, created_credentials: bool) -> None:
        noun = self.fetch_success_noun
        if created:
            self.message_user(
                request,
                _("%(noun)s %(name)s created from AWS data.") % {"noun": noun, "name": obj.name},
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("%(noun)s %(name)s updated from AWS data.") % {"noun": noun, "name": obj.name},
                messages.SUCCESS,
            )

        if created_credentials:
            self.message_user(
                request,
                _("Stored new AWS credentials linked to this %(kind)s.")
                % {"kind": noun.lower()},
                messages.INFO,
            )

    def _record_fetch_discovery(self, request, *, obj, created: bool, form) -> None:
        discovery = start_discovery(
            self.fetch_action_label,
            request,
            model=self.model,
            metadata={"action": self.fetch_discovery_action, "region": form.cleaned_data["region"]},
        )
        if discovery:
            record_discovery_item(
                discovery,
                obj=obj,
                label=obj.name,
                created=created,
                overwritten=not created,
                data={"region": obj.region},
            )

    def fetch_view(self, request):
        self._check_fetch_permission(request)
        form = self.fetch_form_class(request.POST or None)
        context = self._fetch_template_context(request, form)

        if request.method == "POST" and form.is_valid():
            credentials, created_credentials = self.resolve_credentials(form)
            try:
                details = self.fetch_service(**self._fetch_service_kwargs(form, credentials))
            except LightsailFetchError as exc:
                self.message_user(request, str(exc), messages.ERROR)
            else:
                defaults = self.fetch_parse_details(details)
                defaults = self._apply_fetch_defaults(defaults, form, credentials)
                obj, created = self.fetch_update_or_create_target(
                    **self._fetch_lookup_kwargs(form),
                    defaults=defaults,
                )
                self._record_fetch_discovery(request, obj=obj, created=created, form=form)
                self._fetch_success_messages(
                    request,
                    obj=obj,
                    created=created,
                    created_credentials=created_credentials,
                )
                return HttpResponseRedirect(context["changelist_url"])

        return TemplateResponse(request, self.fetch_template_name, context)
