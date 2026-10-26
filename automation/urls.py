from django.urls import path

from . import views

app_name = "automation"

urlpatterns = [
    path("notifications/", views.notifications_list, name="notifications"),
    path("notifications/<int:notification_id>/read/", views.mark_notification_read, name="notification_read"),
    path("assistant/", views.assistant_home, name="assistant"),
    path("assistant/chat/", views.assistant_chat, name="assistant_chat"),
    path("groups/<int:gid>/recurring/", views.recurring_expense_list, name="recurring_list"),
    path("groups/<int:gid>/recurring/new/", views.recurring_expense_create, name="recurring_create"),
    path("groups/<int:gid>/recurring/<int:pk>/", views.recurring_expense_detail, name="recurring_detail"),
    path("groups/<int:gid>/recurring/<int:pk>/edit/", views.recurring_expense_update, name="recurring_edit"),
    path("groups/<int:gid>/recurring/<int:pk>/delete/", views.recurring_expense_delete, name="recurring_delete"),
    path("groups/<int:gid>/recurring/<int:pk>/rules/new/", views.reminder_rule_create, name="reminder_create"),
    path("groups/<int:gid>/recurring/<int:pk>/rules/<int:rule_id>/edit/", views.reminder_rule_update, name="reminder_edit"),
    path("groups/<int:gid>/recurring/<int:pk>/rules/<int:rule_id>/delete/", views.reminder_rule_delete, name="reminder_delete"),
]
