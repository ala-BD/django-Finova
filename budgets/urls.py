from django.urls import path

from . import views

app_name = "budgets"

group_prefix = "groups/<int:gid>/"

urlpatterns = [
    path("budgets/", views.BudgetsOverviewView.as_view(), name="overview"),
    # Budgets d'un groupe
    path(f"{group_prefix}budgets/", views.BudgetListView.as_view(), name="list"),
    path(f"{group_prefix}budgets/new/", views.BudgetCreateView.as_view(), name="create"),
    path(f"{group_prefix}budgets/<int:pk>/edit/", views.BudgetUpdateView.as_view(), name="update"),
    path(f"{group_prefix}budgets/<int:pk>/delete/", views.BudgetDeleteView.as_view(), name="delete"),
    path(f"{group_prefix}budgets/simulate/", views.SimulateView.as_view(), name="simulate"),
    path(f"{group_prefix}budgets/recommend/", views.RecommendView.as_view(), name="recommend"),
    # API JSON de l'IA
    path(f"{group_prefix}budgets/report.pdf", views.BudgetReportPdfView.as_view(), name="report_pdf"),
    path(f"{group_prefix}budgets/<int:pk>/insight/", views.BudgetInsightApiView.as_view(), name="insight_api"),
    # Catégories d'un groupe
    path(f"{group_prefix}categories/", views.CategoryListView.as_view(), name="categories"),
    path(f"{group_prefix}categories/new/", views.CategoryCreateView.as_view(), name="category_create"),
    path(f"{group_prefix}categories/<int:pk>/edit/", views.CategoryUpdateView.as_view(), name="category_update"),
    path(f"{group_prefix}categories/<int:pk>/delete/", views.CategoryDeleteView.as_view(), name="category_delete"),
]
