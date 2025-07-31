from django.urls import path
from . import views

urlpatterns = [
    path('', views.todo_list, name='todo-list'),
    path('<int:pk>/toggle/', views.todo_toggle, name='todo-toggle'),
]
