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
from django.urls import NoReverseMatch, reverse


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
        "core.package": "teams",
        "core.packagerelease": "teams",
    }

    def _get_docs_app_config(self, meta):
        override_label = self.GROUP_OVERRIDES.get(meta.label_lower)
        if override_label:
            return apps.get_app_config(override_label)
        return meta.app_config

    def get_context_data(self, **kwargs):
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
        models.sort(key=lambda m: str(m.app_config.verbose_name))
        return super().get_context_data(**{**kwargs, "models": models})


class ModelGraphIndexView(BaseAdminDocsView):
    template_name = "admin_doc/model_graphs.html"

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
