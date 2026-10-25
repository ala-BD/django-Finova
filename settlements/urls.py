from django.urls import path

from . import views

app_name = "settlements"

urlpatterns = [
    path("", views.placeholder, name="list"),
]
