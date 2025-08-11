from django.urls import path

from website.views import app_index

from . import views

urlpatterns = [
    path("", app_index, {"module": __name__}, name="index"),
    path("todos/", views.todo_list, name="todo-list"),
    path("todos/<int:pk>/toggle/", views.todo_toggle, name="todo-toggle"),
]
