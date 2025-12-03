import argparse
import inspect
from types import SimpleNamespace

from django.apps import apps
from django.contrib import admin
from django.core.management import get_commands, load_command_class
from django.contrib.admindocs.views import (
    BaseAdminDocsView,
    user_has_model_view_permission,
)
from django.shortcuts import render
from django.template import loader
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _
from django.test import signals as test_signals


class CommandsView(BaseAdminDocsView):
    template_name = "admin_doc/commands.html"

    def get_context_data(self, **kwargs):
        commands = []
        for name, app_name in sorted(get_commands().items()):
            try:
                cmd = load_command_class(app_name, name)
                parser = cmd.create_parser("manage.py", name)
            except Exception:  # pragma: no cover - command import issues
                continue
            args = []
            options = []
            for action in parser._actions:
                if isinstance(action, argparse._HelpAction):
                    continue
                if action.option_strings:
                    options.append(
                        {
                            "opts": ", ".join(action.option_strings),
                            "help": action.help or "",
                        }
                    )
                else:
                    args.append(
                        {
                            "name": action.metavar or action.dest,
                            "help": action.help or "",
                        }
                    )
            commands.append(
                {
                    "name": name,
                    "help": getattr(cmd, "help", ""),
                    "args": args,
                    "options": options,
                }
            )
        return super().get_context_data(**{**kwargs, "commands": commands})


class OrderedModelIndexView(BaseAdminDocsView):
    template_name = "admin_doc/model_index.html"

    GROUP_OVERRIDES = {
        "ocpp.location": "core",
        "core.rfid": "ocpp",
        "ocpp.cpforwarder": "ocpp",
        "core.package": "teams",
        "core.packagerelease": "teams",
    }

    @staticmethod
    def _get_application_model():
        try:
            return apps.get_model("app", "Application")
        except LookupError:
            return None

    def _application_order_map(self) -> dict[str, int]:
        return {}

    def _group_sort_key(self, app_config, order_map: dict[str, int]):
        Application = self._get_application_model()
        name = str(app_config.label)
        if Application:
            name = Application.format_display_name(name)
        return name, app_config.label

    def _group_models(
        self, models: list[SimpleNamespace], order_map: dict[str, int]
    ) -> list[dict[str, object]]:
        Application = self._get_application_model()
        grouped: dict[str, dict[str, object]] = {}

        for model in models:
            app_config = model.app_config
            group_name = str(app_config.label)
            if Application:
                group_name = Application.format_display_name(group_name)
            sort_key = self._group_sort_key(app_config, order_map)

            group = grouped.setdefault(
                group_name,
                {
                    "name": group_name,
                    "label": app_config.label,
                    "app_name": app_config.name,
                    "order": sort_key,
                    "models": [],
                },
            )

            if sort_key < group["order"]:
                group["label"] = app_config.label
                group["app_name"] = app_config.name
                group["order"] = sort_key

            group["models"].append(model)

        ordered_groups = sorted(grouped.values(), key=lambda group: group["order"])
        for group in ordered_groups:
            group["models"].sort(key=lambda model: model.object_name)
        return ordered_groups

    def _get_docs_app_config(self, meta):
        override = self.GROUP_OVERRIDES.get(meta.label_lower)
        if override:
            if isinstance(override, str):
                return apps.get_app_config(override)
            return override
        return meta.app_config

    def get_context_data(self, **kwargs):
        order_map = self._application_order_map()
        models = []
        for m in apps.get_models():
            if user_has_model_view_permission(self.request.user, m._meta):
                meta = m._meta
                meta.docstring = inspect.getdoc(m) or ""
                app_config = self._get_docs_app_config(meta)
                models.append(
                    SimpleNamespace(
                        app_label=meta.app_label,
                        model_name=meta.model_name,
                        object_name=meta.object_name,
                        docstring=meta.docstring,
                        app_config=app_config,
                    )
                )
        models.sort(
            key=lambda m: (
                self._group_sort_key(m.app_config, order_map), m.object_name
            )
        )
        grouped_models = self._group_models(models, order_map)
        return super().get_context_data(
            **{**kwargs, "models": models, "grouped_models": grouped_models}
        )


class ModelGraphIndexView(BaseAdminDocsView):
    template_name = "admin_doc/model_graphs.html"

    def render_to_response(self, context, **response_kwargs):
        template_name = response_kwargs.pop("template_name", None)
        if template_name is None:
            template_name = self.get_template_names()
        response = render(
            self.request,
            template_name,
            context,
            **response_kwargs,
        )
        if getattr(response, "context", None) is None:
            response.context = context
        if test_signals.template_rendered.receivers:
            if isinstance(template_name, (list, tuple)):
                template = loader.select_template(template_name)
            else:
                template = loader.get_template(template_name)
            signal_context = context
            if self.request is not None and "request" not in signal_context:
                signal_context = {**context, "request": self.request}
            test_signals.template_rendered.send(
                sender=template.__class__,
                template=template,
                context=signal_context,
            )
        return response

    def get_context_data(self, **kwargs):
        sections = {}
        user = self.request.user

        for model in admin.site._registry:
            meta = model._meta
            if not user_has_model_view_permission(user, meta):
                continue

            app_config = apps.get_app_config(meta.app_label)
            section = sections.setdefault(
                app_config.label,
                {
                    "app_label": app_config.label,
                    "verbose_name": str(app_config.verbose_name),
                    "models": [],
                },
            )

            section["models"].append(
                {
                    "object_name": meta.object_name,
                    "verbose_name": str(meta.verbose_name),
                    "doc_url": reverse(
                        "django-admindocs-models-detail",
                        kwargs={
                            "app_label": meta.app_label,
                            "model_name": meta.model_name,
                        },
                    ),
                }
            )

        graph_sections = []
        for section in sections.values():
            section_models = section["models"]
            section_models.sort(key=lambda model: model["verbose_name"])

            try:
                app_list_url = reverse("admin:app_list", args=[section["app_label"]])
            except NoReverseMatch:
                app_list_url = ""

            graph_sections.append(
                {
                    **section,
                    "graph_url": reverse(
                        "admin-model-graph", args=[section["app_label"]]
                    ),
                    "app_list_url": app_list_url,
                    "model_count": len(section_models),
                }
            )

        graph_sections.sort(key=lambda section: section["verbose_name"])

        return super().get_context_data(**{**kwargs, "sections": graph_sections})
