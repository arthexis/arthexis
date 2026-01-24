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
    results = None
    error_message = ""
    ran_query = False

    if should_run and not errors:
        try:
            results = query.execute(values, resolve_value_sigils=False)
            ran_query = True
        except RuntimeError as exc:
            logger.warning(
                "Configuration error executing Odoo query %s: %s", query.pk, exc
            )
            error_message = str(exc)
        except Exception:
            logger.exception("Unable to execute Odoo query %s", query.pk)
            error_message = _("Unable to execute query.")

    rendered_variables = [
        variable.to_context(values.get(variable.key)) for variable in variables
    ]

    context = {
        "query": query,
        "variables": rendered_variables,
        "errors": errors,
        "results": results,
        "results_json": json.dumps(results, indent=2, default=str) if results else "",
        "error_message": error_message,
        "ran_query": ran_query,
    }
    return render(request, "odoo/public_query.html", context)
