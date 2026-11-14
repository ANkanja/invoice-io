from django.urls import path

from . import views

app_name = "restock"

urlpatterns = [
    path("", views.plan_list, name="list"),
    path("<int:pk>/", views.plan_detail, name="detail"),
    path("<int:pk>/add/", views.line_add, name="line_add"),
    path("<int:pk>/lines/<int:line_pk>/buy/", views.purchase_create, name="purchase"),
    path("<int:pk>/purchases/<int:purchase_pk>/reverse/", views.purchase_reverse, name="reverse"),
]
