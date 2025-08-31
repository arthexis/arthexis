from django.views.generic import DetailView, ListView
from .models import GamePortal, GameMaterial


class GameListView(ListView):
    model = GamePortal
    template_name = "game/game_list.html"
    context_object_name = "games"


class GameDetailView(DetailView):
    model = GamePortal
    template_name = "game/game_detail.html"
    context_object_name = "game"


class GameMaterialView(DetailView):
    model = GameMaterial
    template_name = "game/material_detail.html"
    context_object_name = "material"
