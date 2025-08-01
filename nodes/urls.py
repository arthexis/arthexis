from django.urls import path

from . import views

urlpatterns = [
    path('list/', views.node_list, name='node-list'),
    path('register/', views.register_node, name='register-node'),
    path('screenshot/', views.capture, name='node-screenshot'),
]
