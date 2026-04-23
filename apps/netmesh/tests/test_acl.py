import pytest
from django.contrib.sites.models import Site

from apps.netmesh.models import MeshMembership, PeerPolicy
from apps.netmesh.services import ACLResolver
from apps.nodes.models import Node, NodeRole
from apps.ocpp.models import Charger

