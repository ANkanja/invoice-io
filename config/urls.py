from django.contrib import admin
from django.urls import include, path

from core.views import protected_media

admin.site.site_header = "invoicing_io data admin"
admin.site.site_title = "invoicing_io admin"

urlpatterns = [
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("products/", include("products.urls")),
    path("customers/", include("customers.urls")),
    path("stock/", include("inventory.urls")),
    path("invoices/", include("invoices.urls")),
    path("payments/", include("payments.urls")),
    path("posters/", include("posters.urls")),
    path("admin/", admin.site.urls),
    # Uploaded files are never public: served only to logged-in users.
    path("media/<path:path>", protected_media, name="media"),
]
