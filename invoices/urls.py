from django.urls import path

from . import views

app_name = "invoices"

urlpatterns = [
    path("", views.invoice_list, name="list"),
    path("new/", views.invoice_create, name="create"),
    path("<int:pk>/", views.invoice_detail, name="detail"),
    path("<int:pk>/edit/", views.invoice_edit, name="edit"),
    path("<int:pk>/amend/", views.invoice_amend, name="amend"),
    path("<int:pk>/revisions/<int:revision_no>/", views.invoice_revision, name="revision"),
    path("<int:pk>/revisions/<int:revision_no>/pdf/", views.invoice_revision_pdf, name="revision_pdf"),
    path("<int:pk>/issue/", views.invoice_issue, name="issue"),
    path("<int:pk>/cancel/", views.invoice_cancel, name="cancel"),
    path("<int:pk>/replace/", views.invoice_replace, name="replace"),
    path("<int:pk>/duplicate/", views.invoice_duplicate, name="duplicate"),
    path("<int:pk>/delete/", views.invoice_delete, name="delete"),
    path("<int:pk>/pdf/", views.invoice_pdf, name="pdf"),
]
