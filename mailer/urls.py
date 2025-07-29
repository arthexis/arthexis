from django.urls import path

from . import views

urlpatterns = [
    path('template/add/', views.add_template, name='add-template'),
    path('queue/add/', views.queue_email, name='queue-email'),
    path('status/<int:qid>/', views.email_status, name='email-status'),
    path('purge/', views.purge, name='purge-queue'),
]
