from django.urls import path

from . import views

app_name = "groups"

urlpatterns = [
    path("", views.placeholder, name="list"),
]
