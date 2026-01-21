import argparse
import inspect
from types import SimpleNamespace

from django.apps import apps
from django.contrib import admin
from django.core.management import get_commands, load_command_class
from django.contrib.admindocs import utils
from django.contrib.admindocs.views import (
    BaseAdminDocsView,
    MODEL_METHODS_EXCLUDE,
    func_accepts_kwargs,
    func_accepts_var_args,
    get_func_full_args,
    get_readable_field_data_type,
    get_return_data_type,
    method_has_no_args,
    user_has_model_view_permission,
)
from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import Http404
from django.shortcuts import render
from django.template import loader
from django.urls import NoReverseMatch, reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.utils.html import strip_tags
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


class ModelDetailDocsView(BaseAdminDocsView):
    template_name = "admin_doc/model_detail.html"

    def _parse_docstring(self, docstring: str | None, opts) -> str:
        if not docstring:
            return ""
        return utils.parse_rst(
            inspect.cleandoc(docstring),
            "model",
            _("model:") + opts.model_name,
        )

    def _method_arguments(self, func) -> str:
        arguments = get_func_full_args(func)
        return ", ".join(
            [
                "=".join([arg_el[0], *map(repr, arg_el[1:])])
                for arg_el in arguments
            ]
        )

    def _should_exclude_method(self, func_name: str) -> bool:
        return any(func_name.startswith(exclude) for exclude in MODEL_METHODS_EXCLUDE)

    def _build_model_methods(self, model, opts) -> list[dict[str, str]]:
        methods = []
        for func_name, func in model.__dict__.items():
            if self._should_exclude_method(func_name):
                continue
            if not inspect.isfunction(func):
                continue
            methods.append(
                {
                    "name": func_name,
                    "arguments": self._method_arguments(func),
                    "verbose": self._parse_docstring(func.__doc__, opts),
                }
            )
        return methods

    def _build_manager_methods(self, manager, opts) -> list[dict[str, str]]:
        if manager is None:
            return []
        methods = []
        for func_name, func in manager.__class__.__dict__.items():
            if self._should_exclude_method(func_name):
                continue
            if not inspect.isfunction(func):
                continue
            methods.append(
                {
                    "name": func_name,
                    "arguments": self._method_arguments(func),
                    "verbose": self._parse_docstring(func.__doc__, opts),
                }
            )
        return methods

    def get_context_data(self, **kwargs):
        model_name = self.kwargs["model_name"]
        try:
            app_config = apps.get_app_config(self.kwargs["app_label"])
        except LookupError:
            raise Http404(_("App %(app_label)r not found") % self.kwargs)
        try:
            model = app_config.get_model(model_name)
        except LookupError:
            raise Http404(
                _("Model %(model_name)r not found in app %(app_label)r") % self.kwargs
            )

        opts = model._meta
        if not user_has_model_view_permission(self.request.user, opts):
            raise PermissionDenied

        title, body, metadata = utils.parse_docstring(model.__doc__)
        title = title and utils.parse_rst(title, "model", _("model:") + model_name)
        body = body and utils.parse_rst(body, "model", _("model:") + model_name)

        fields = []
        for field in opts.fields:
            if isinstance(field, models.ForeignKey):
                data_type = field.remote_field.model.__name__
                app_label = field.remote_field.model._meta.app_label
                verbose = utils.parse_rst(
                    (
                        _("the related `%(app_label)s.%(data_type)s` object")
                        % {
                            "app_label": app_label,
                            "data_type": data_type,
                        }
                    ),
                    "model",
                    _("model:") + data_type,
                )
            else:
                data_type = get_readable_field_data_type(field)
                verbose = field.verbose_name
            fields.append(
                {
                    "name": field.name,
                    "data_type": data_type,
                    "verbose": verbose or "",
                    "help_text": field.help_text,
                }
            )

        for field in opts.many_to_many:
            data_type = field.remote_field.model.__name__
            app_label = field.remote_field.model._meta.app_label
            verbose = _("related `%(app_label)s.%(object_name)s` objects") % {
                "app_label": app_label,
                "object_name": data_type,
            }
            fields.append(
                {
                    "name": f"{field.name}.all",
                    "data_type": "List",
                    "verbose": utils.parse_rst(
                        _("all %s") % verbose, "model", _("model:") + opts.model_name
                    ),
                }
            )
            fields.append(
                {
                    "name": f"{field.name}.count",
                    "data_type": "Integer",
                    "verbose": utils.parse_rst(
                        _("number of %s") % verbose,
                        "model",
                        _("model:") + opts.model_name,
                    ),
                }
            )

        for func_name, func in model.__dict__.items():
            if self._should_exclude_method(func_name):
                continue
            if isinstance(func, (cached_property, property)):
                verbose = self._parse_docstring(func.__doc__, opts)
                fields.append(
                    {
                        "name": func_name,
                        "data_type": get_return_data_type(func_name),
                        "verbose": verbose or "",
                    }
                )
            elif (
                inspect.isfunction(func)
                and method_has_no_args(func)
                and not func_accepts_kwargs(func)
                and not func_accepts_var_args(func)
            ):
                verbose = self._parse_docstring(func.__doc__, opts)
                fields.append(
                    {
                        "name": func_name,
                        "data_type": get_return_data_type(func_name),
                        "verbose": verbose or "",
                    }
                )

        for rel in opts.related_objects:
            verbose = _("related `%(app_label)s.%(object_name)s` objects") % {
                "app_label": rel.related_model._meta.app_label,
                "object_name": rel.related_model._meta.object_name,
            }
            accessor = rel.accessor_name
            fields.append(
                {
                    "name": f"{accessor}.all",
                    "data_type": "List",
                    "verbose": utils.parse_rst(
                        _("all %s") % verbose, "model", _("model:") + opts.model_name
                    ),
                }
            )
            fields.append(
                {
                    "name": f"{accessor}.count",
                    "data_type": "Integer",
                    "verbose": utils.parse_rst(
                        _("number of %s") % verbose,
                        "model",
                        _("model:") + opts.model_name,
                    ),
                }
            )

        model_methods = sorted(
            self._build_model_methods(model, opts), key=lambda method: method["name"]
        )
        manager = getattr(model, "_default_manager", None)
        manager_methods = sorted(
            self._build_manager_methods(manager, opts),
            key=lambda method: method["name"],
        )

        return super().get_context_data(
            **{
                **kwargs,
                "name": opts.label,
                "summary": strip_tags(title or ""),
                "description": body,
                "fields": fields,
                "model_methods": model_methods,
                "manager_methods": manager_methods,
                "manager_name": manager.__class__.__name__ if manager else "",
            }
        )
