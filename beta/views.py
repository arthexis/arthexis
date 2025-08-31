from django.views.generic import DetailView, ListView
from .models import GamePortal, GameMaterial


class GamePortalListView(ListView):
    model = GamePortal
    template_name = "beta/portal_list.html"
    context_object_name = "games"


class GamePortalDetailView(DetailView):
    model = GamePortal
    template_name = "beta/portal_detail.html"
    context_object_name = "game"


class GameMaterialView(DetailView):
    model = GameMaterial
    template_name = "beta/material_detail.html"
    context_object_name = "material"
