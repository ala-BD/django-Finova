from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("summary/", views.summary, name="summary"),
    path("filters/", views.SavedFilterListView.as_view(), name="filter_list"),
    path("filters/new/", views.SavedFilterCreateView.as_view(), name="filter_create"),
    path("filters/<int:pk>/edit/", views.SavedFilterUpdateView.as_view(), name="filter_edit"),
    path("filters/<int:pk>/delete/", views.SavedFilterDeleteView.as_view(), name="filter_delete"),
    path("filters/<int:pk>/apply/", views.filter_apply, name="filter_apply"),
    path("reports/", views.ReportListView.as_view(), name="report_list"),
    path("reports/new/", views.ReportCreateView.as_view(), name="report_create"),
    path("reports/<int:pk>/", views.ReportDetailView.as_view(), name="report_detail"),
    path("reports/<int:pk>/delete/", views.ReportDeleteView.as_view(), name="report_delete"),
    path("reports/<int:pk>/download/", views.report_download, name="report_download"),
    path("anomalies/", views.anomaly_list, name="anomaly_list"),
    path("anomalies/scan/", views.anomaly_scan, name="anomaly_scan"),
    path("anomalies/<int:pk>/review/", views.anomaly_review, name="anomaly_review"),
]
