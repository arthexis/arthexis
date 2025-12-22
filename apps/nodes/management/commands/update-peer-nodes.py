"""Manually run the peer node update task and report results."""

from __future__ import annotations

from apps.nodes.tasks import poll_peers

from .check_nodes import Command as CheckNodesCommand


class Command(CheckNodesCommand):
    """Run the update-peer-nodes workflow and display a status table."""

    help = "Refresh peer node information using the scheduled update workflow."

    def handle(self, *args, **options):
        summary = poll_peers()
        self._report_summary(summary)
