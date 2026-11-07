from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.payment_list, name="list"),
    path("new/", views.payment_create, name="create"),
    path("<int:pk>/", views.payment_detail, name="detail"),
    path("<int:pk>/apply/", views.payment_apply, name="apply"),
    path("<int:pk>/apply/<int:invoice_pk>/", views.apply_to_invoice, name="apply_to_invoice"),
    path("<int:pk>/reverse/", views.payment_reverse, name="reverse"),
    path("<int:pk>/receipt/", views.payment_receipt, name="receipt"),
]
