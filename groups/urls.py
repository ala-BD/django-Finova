from django.urls import path

from . import views

app_name = "groups"

urlpatterns = [
    path("", views.GroupListView.as_view(), name="list"),
    path("new/", views.GroupCreateView.as_view(), name="create"),
    path("new/ai/", views.GroupAICreateView.as_view(), name="ai_create"),
    path("<int:gid>/", views.GroupDetailView.as_view(), name="detail"),
    path("<int:gid>/edit/", views.GroupUpdateView.as_view(), name="edit"),
    path("<int:gid>/delete/", views.GroupArchiveView.as_view(), name="delete"),
    path("<int:gid>/leave/", views.GroupLeaveView.as_view(), name="leave"),
    path("<int:gid>/invite/", views.InvitationCreateView.as_view(), name="invite"),
    path("<int:gid>/charter/", views.CharterView.as_view(), name="charter"),
    path("<int:gid>/charter/edit/", views.CharterEditView.as_view(), name="charter_edit"),
    path("<int:gid>/charter/generate/", views.CharterGenerateView.as_view(), name="charter_generate"),
    path("<int:gid>/equity/", views.EquityCoachView.as_view(), name="equity"),
    path("<int:gid>/equity/ask/", views.EquityCoachAskView.as_view(), name="equity_ask"),
    path("<int:gid>/equity/apply/", views.ApplySplitRuleView.as_view(), name="equity_apply"),
    path(
        "<int:gid>/members/<int:mid>/edit/",
        views.MembershipUpdateView.as_view(),
        name="member_edit",
    ),
]
