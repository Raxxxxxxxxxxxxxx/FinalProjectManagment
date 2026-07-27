from django.contrib import admin
from django.urls import include, path

from marketplace import views

handler403 = "marketplace.views.error_403"
handler404 = "marketplace.views.error_404"
handler500 = "marketplace.views.error_500"

urlpatterns = [
    path("health/", views.health_check, name="health_check"),
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("accounts/", include("accounts.urls")),
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("requests/", include("marketplace.urls")),
]
