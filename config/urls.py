from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core.views import home

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", home, name="home"),
    path("accounts/", include("accounts.urls")),
    path("groups/", include("groups.urls")),
    path("invitations/", include("groups.invitation_urls")),
    path("expenses/", include("expenses.urls")),
    path("settlements/", include("settlements.urls")),
    path("budgets/", include("budgets.urls")),
    path("analytics/", include("analytics.urls")),
    path("automation/", include("automation.urls")),
]

handler403 = "core.views.error_403"
handler404 = "core.views.error_404"
handler500 = "core.views.error_500"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
