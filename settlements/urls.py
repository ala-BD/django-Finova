from django.urls import path

from . import views

app_name = "settlements"

# Monté sous /groups/<gid>/ (config/urls.py) : ressources d'un groupe
urlpatterns = [
    path("balances/", views.BalancesView.as_view(), name="balances"),
    path("settlements/", views.SettlementListView.as_view(), name="list"),
    path("settlements/new/", views.SettlementCreateView.as_view(), name="create"),
    path("settlements/parse/", views.SettlementParseView.as_view(), name="parse"),
    path("settlements/<int:sid>/", views.SettlementDetailView.as_view(), name="detail"),
    path(
        "settlements/<int:sid>/confirm/",
        views.SettlementDecisionView.as_view(action="confirm"),
        name="confirm",
    ),
    path(
        "settlements/<int:sid>/reject/",
        views.SettlementDecisionView.as_view(action="reject"),
        name="reject",
    ),
    path("settlements/<int:sid>/plan/new/", views.PlanFormView.as_view(), name="plan_create"),
    path(
        "settlements/<int:sid>/plan/edit/",
        views.PlanFormView.as_view(editing=True),
        name="plan_edit",
    ),
    path("settlements/<int:sid>/plan/delete/", views.PlanDeleteView.as_view(), name="plan_delete"),
    path("settlements/<int:sid>/plan/suggest/", views.PlanSuggestView.as_view(), name="plan_suggest"),
    path("settlements/<int:sid>/plan/pdf/", views.PlanExportView.as_view(fmt="pdf"), name="plan_pdf"),
    path("settlements/<int:sid>/plan/ics/", views.PlanExportView.as_view(fmt="ics"), name="plan_ics"),
    path("settlements/<int:sid>/risk/", views.RiskPredictView.as_view(), name="risk"),
    path("settlements/<int:sid>/simulate/", views.RiskSimulatorView.as_view(), name="simulate"),
    path("settlements/<int:sid>/reminder/", views.ReminderGenerateView.as_view(), name="reminder"),
    path(
        "settlements/<int:sid>/reminder/send/",
        views.ReminderSendView.as_view(),
        name="reminder_send",
    ),
    path("installments/<int:iid>/pay/", views.InstallmentPayView.as_view(), name="installment_pay"),
]
