from django.urls import path

from . import views

app_name = "posters"

urlpatterns = [
    path("", views.poster_list, name="list"),
    path("new/", views.poster_new, name="new"),
    path("create/", views.poster_create, name="create"),
    path("<int:pk>/", views.poster_edit, name="edit"),
    path("<int:pk>/preview/", views.poster_preview, name="preview"),
    path("exports/<int:pk>/", views.export_detail, name="export"),
    path("exports/<int:pk>/image/", views.export_image, name="export_image"),
    path("designs/", views.design_list, name="designs"),
    path("designs/starters/", views.starter_designs_create, name="starter_designs"),
    path("designs/new/", views.design_create, name="design_create"),
    path("designs/<int:pk>/", views.design_edit, name="design_edit"),
    path("designs/<int:pk>/sample/", views.design_sample, name="design_sample"),
    path("designs/<int:pk>/archive/", views.design_set_active, {"active": False}, name="design_archive"),
    path("designs/<int:pk>/restore/", views.design_set_active, {"active": True}, name="design_restore"),
    path("fonts/", views.font_list, name="fonts"),
    path("calendar/", views.occasion_list, name="calendar"),
    path("calendar/starters/", views.occasion_starters, name="calendar_starters"),
    path("calendar/new/", views.occasion_edit, name="occasion_create"),
    path("calendar/<int:pk>/", views.occasion_edit, name="occasion_edit"),
]
