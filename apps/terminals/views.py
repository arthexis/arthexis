from django.views.generic import DetailView, ListView

from .models import AgentTerminal


class AgentTerminalListView(ListView):
    """Starter list view for agent terminals."""

    model = AgentTerminal
    template_name = "terminals/agent_terminal_list.html"
    context_object_name = "agent_terminals"


class AgentTerminalDetailView(DetailView):
    """Starter detail view for agent terminals."""

    model = AgentTerminal
    template_name = "terminals/agent_terminal_detail.html"
    context_object_name = "agent_terminal"
