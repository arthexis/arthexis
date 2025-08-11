from django.urls import path

from . import views

urlpatterns = [
    path("todos/", views.todo_list, name="todo-list"),
    path("todos/<int:pk>/toggle/", views.todo_toggle, name="todo-toggle"),
]
