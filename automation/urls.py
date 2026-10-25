from django.urls import path

from . import views

app_name = "automation"

urlpatterns = [
    path("notifications/", views.placeholder, name="notifications"),
    path("assistant/", views.placeholder, name="assistant"),
]
