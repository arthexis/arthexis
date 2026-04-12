from __future__ import annotations

import json
import logging

from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _

from .models import OdooQuery

logger = logging.getLogger(__name__)


def query_public_view(request, slug: str):
    query = get_object_or_404(
        OdooQuery,
        public_view_slug=slug,
        enable_public_view=True,
    )
    variables = list(query.variables.order_by("sort_order", "key"))
    values: dict[str, str] = {}
    errors: dict[str, str] = {}

    for variable in variables:
        raw_value = request.GET.get(variable.key)
        value = raw_value if raw_value is not None else variable.default_value
        value = value or ""
        if variable.is_required and not value.strip():
            errors[variable.key] = _("This field is required.")
        values[variable.key] = value

    should_run = bool(request.GET) or any(variable.default_value for variable in variables)
    execution_allowed = bool(request.user.is_authenticated and request.user.is_staff)
    results = None
    error_message = ""
    ran_query = False

    if should_run and not errors and execution_allowed:
        try:
            results = query.execute(values)
            ran_query = True
        except RuntimeError as exc:
            logger.warning(
                "Configuration error executing Odoo query %s: %s",
                query.pk,
                exc,
            )
            error_message = str(exc)
        except Exception:
            logger.exception("Unable to execute Odoo query %s", query.pk)
            error_message = _("Unable to execute query.")
    elif should_run and not execution_allowed:
        error_message = _(
            "Execution is restricted to authenticated staff users. Public access is metadata-only."
        )

    rendered_variables = []
    for variable in variables:
        variable_context = variable.to_context(values.get(variable.key))
        variable_context["error"] = errors.get(variable.key, "")
        rendered_variables.append(variable_context)

    context = {
        "query": query,
        "variables": rendered_variables,
        "errors": errors,
        "results": results,
        "results_json": json.dumps(results, indent=2, default=str) if results else "",
        "error_message": error_message,
        "execution_allowed": execution_allowed,
        "ran_query": ran_query,
    }
    return render(request, "odoo/public_query.html", context)
