from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from apps.nodes.models import Node
from apps.sites.utils import landing

from .forms import MaintenanceRequestForm


@login_required(login_url="pages:login")
@landing("Maintenance Request")
def maintenance_request(request):
    """Allow authenticated users to schedule manual maintenance tasks."""

    form = MaintenanceRequestForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        task = form.save(commit=False)
        task.requestor = request.user if request.user.is_authenticated else None
        task.assigned_user = request.user if request.user.is_authenticated else None
        task.node = task.node or Node.get_local()
        task.is_user_data = True
        task.save()
        form.save_m2m()
        messages.success(
            request,
            _("Maintenance request scheduled for %(location)s.")
            % {"location": task.location or _("the selected location")},
        )
        return redirect("tasks:maintenance-request")

    return render(request, "tasks/maintenance_request.html", {"form": form})
