from django.urls import path

from . import views

app_name = "groups"

urlpatterns = [
    path("", views.GroupListView.as_view(), name="list"),
    path("new/", views.GroupCreateView.as_view(), name="create"),
    path("new/ai/", views.GroupAICreateView.as_view(), name="ai_create"),
    path("<int:gid>/", views.GroupDetailView.as_view(), name="detail"),
    path(
        "<int:gid>/equity-coach/",
        views.EquityCoachView.as_view(),
        name="equity_coach",
    ),
    path("<int:gid>/edit/", views.GroupUpdateView.as_view(), name="edit"),
    path("<int:gid>/delete/", views.GroupArchiveView.as_view(), name="delete"),
    path("<int:gid>/leave/", views.GroupLeaveView.as_view(), name="leave"),
    path("<int:gid>/invite/", views.InvitationCreateView.as_view(), name="invite"),
    path(
        "<int:gid>/members/<int:mid>/edit/",
        views.MembershipUpdateView.as_view(),
        name="member_edit",
    ),
]
