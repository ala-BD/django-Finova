from django.urls import path

from . import views

app_name = "expenses"

urlpatterns = [
    path("", views.ExpenseListView.as_view(), name="list"),
    path("new/", views.ExpenseCreateView.as_view(), name="create"),
    path("<int:pk>/", views.ExpenseDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.ExpenseUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.ExpenseDeleteView.as_view(), name="delete"),
    path("<int:pk>/items/add/", views.ExpenseItemCreateView.as_view(), name="item_add"),
    path("<int:pk>/items/<int:item_id>/claim/", views.ItemClaimToggleView.as_view(), name="item_claim"),
    path("categorize/", views.ExpenseCategorizeView.as_view(), name="categorize"),
    path("nlp-extract/", views.ExpenseNLPExtractView.as_view(), name="nlp_extract"),
]
