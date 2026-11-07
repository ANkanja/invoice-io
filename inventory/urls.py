from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.stock_overview, name="overview"),
    path("in/", views.stock_in, name="stock_in"),
    path("out/", views.stock_out, name="stock_out"),
    path("count/", views.stock_count, name="count"),
    path("return/", views.stock_return, name="return"),
    path("take/", views.stock_take, name="take"),
    path("movements/", views.movement_list, name="movements"),
]
