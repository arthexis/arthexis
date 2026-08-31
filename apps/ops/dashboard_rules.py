"""Dashboard rule handlers for operations visibility and compliance."""

from django.utils.translation import gettext_lazy as _

from apps.counters.dashboard_rules import rule_failure, rule_success

from .models import OperationScreen


def evaluate_required_operations_rule() -> dict[str, object]:
    """Fail when any required operation has no completion records."""

    required = OperationScreen.objects.filter(is_active=True, is_required=True)
    missing = required.exclude(executions__isnull=False).count()
    if missing:
        return rule_failure(
            _("%(count)s required operation(s) have never been completed.") % {"count": missing}
        )
    return rule_success(_("All required operations have at least one completion."))
