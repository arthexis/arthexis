"""Manually run the peer node update task and report results."""

from __future__ import annotations

from apps.nodes.tasks import update_peer_nodes_information

from .check_nodes import Command as CheckNodesCommand


class Command(CheckNodesCommand):
    """Run the update-peer-nodes workflow and display a status table."""

    help = "Refresh peer node information using the scheduled update workflow."

    def handle(self, *args, **options):
        summary = update_peer_nodes_information()
        self._report_summary(summary)
