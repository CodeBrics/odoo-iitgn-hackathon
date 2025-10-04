from django.contrib import admin
from django.urls import path, include
from expenses.views import LoginViewCustom, logout_view, dashboard
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("auth/login/", LoginViewCustom.as_view(), name="login"),
    path("auth/logout/", logout_view, name="logout"),
    path("", dashboard, name="dashboard"),
    path("expenses/", include("expenses.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
