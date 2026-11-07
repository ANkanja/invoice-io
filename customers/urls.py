from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("", views.customer_list, name="list"),
    path("new/", views.customer_create, name="create"),
    path("<int:pk>/", views.customer_detail, name="detail"),
    path("<int:pk>/edit/", views.customer_edit, name="edit"),
    path("<int:pk>/archive/", views.customer_set_active, {"active": False}, name="archive"),
    path("<int:pk>/restore/", views.customer_set_active, {"active": True}, name="restore"),
]
