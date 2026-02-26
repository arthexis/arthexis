"""Ensure workflow transitions are persisted for later reconstruction."""

import pytest

from apps.flows import NodeWorkflow, NodeWorkflowStep
from apps.flows.models import Transition

