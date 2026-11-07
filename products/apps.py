from django.apps import AppConfig
from django.db.models.signals import post_delete, post_migrate, post_save


class ProductsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "products"

    def ready(self):
        from .units import clear_unit_cache

        Unit = self.get_model("Unit")
        post_save.connect(clear_unit_cache, sender=Unit, dispatch_uid="unit_cache_save")
        post_delete.connect(clear_unit_cache, sender=Unit, dispatch_uid="unit_cache_delete")
        post_migrate.connect(clear_unit_cache, dispatch_uid="unit_cache_migrate")
