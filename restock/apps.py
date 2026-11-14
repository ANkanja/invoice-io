from django.apps import AppConfig


class RestockConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "restock"
    verbose_name = "Restock planning"
