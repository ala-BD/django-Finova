from django.urls import path

from . import views

app_name = "invitations"

urlpatterns = [
    path("<uuid:token>/accept/", views.InvitationAcceptView.as_view(), name="accept"),
]
