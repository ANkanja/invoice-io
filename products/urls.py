from django.urls import path

from . import views

app_name = "products"

urlpatterns = [
    path("", views.product_list, name="list"),
    path("new/", views.product_create, name="create"),
    path("<int:pk>/", views.product_detail, name="detail"),
    path("<int:pk>/edit/", views.product_edit, name="edit"),
    path("<int:pk>/archive/", views.product_archive, name="archive"),
    path("<int:pk>/restore/", views.product_restore, name="restore"),
    path("<int:pk>/options/new/", views.option_edit, name="option_create"),
    path("<int:pk>/options/<int:option_pk>/", views.option_edit, name="option_edit"),
    path("categories/", views.category_list, name="categories"),
    path("categories/<int:pk>/edit/", views.category_edit, name="category_edit"),
    path("categories/<int:pk>/archive/", views.category_set_active, {"active": False}, name="category_archive"),
    path("categories/<int:pk>/restore/", views.category_set_active, {"active": True}, name="category_restore"),
]
