from django.shortcuts import get_object_or_404, redirect
from django.views.generic import DetailView, ListView

from .models import GamePortal, GameMaterial, MaterialRegion, create_random_material


class GameListView(ListView):
    model = GamePortal
    template_name = "game/game_list.html"
    context_object_name = "games"


class GameDetailView(DetailView):
    model = GamePortal
    template_name = "game/game_detail.html"
    context_object_name = "game"

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.entry_material:
            self.object.entry_material = create_random_material()
            self.object.save()
        return super().get(request, *args, **kwargs)


class GameMaterialView(DetailView):
    model = GameMaterial
    template_name = "game/material_detail.html"
    context_object_name = "material"


def follow_region(request, pk):
    region = get_object_or_404(MaterialRegion, pk=pk)
    if not region.target:
        region.target = create_random_material()
        region.save()
    return redirect("game:material-detail", region.target.slug)
