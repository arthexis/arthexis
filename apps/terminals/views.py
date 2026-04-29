from django.views.generic import DetailView, ListView

from .models import TerminalsItem


class TerminalsItemListView(ListView):
    """Starter list view for generated model."""

    model = TerminalsItem
    template_name = "terminals/terminals-item_list.html"
    context_object_name = "terminals-item_list"

class TerminalsItemDetailView(DetailView):
    """Starter detail view for generated model."""

    model = TerminalsItem
    template_name = "terminals/terminals-item_detail.html"
    context_object_name = "terminals-item"
