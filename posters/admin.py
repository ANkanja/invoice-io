from django.contrib import admin

from .models import FontAsset, Occasion, Poster, PosterExport, PosterTemplate, TemplateField, TemplateVersion


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Occasion)
class OccasionAdmin(admin.ModelAdmin):
    list_display = ("name", "day", "month", "year", "is_active")
    list_filter = ("is_active", "month")
    search_fields = ("name",)


@admin.register(FontAsset)
class FontAssetAdmin(ReadOnlyAdmin):
    list_display = ("name", "bundled_file", "file")


class TemplateFieldInline(admin.TabularInline):
    model = TemplateField
    extra = 0
    can_delete = False
    fields = ("position", "key", "source", "label", "x", "y", "w", "h", "font", "size")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(TemplateVersion)
class TemplateVersionAdmin(ReadOnlyAdmin):
    list_display = ("template", "version_no", "width", "height", "is_locked", "created_at")
    inlines = [TemplateFieldInline]


@admin.register(PosterTemplate)
class PosterTemplateAdmin(ReadOnlyAdmin):
    list_display = ("name", "is_starter", "is_active")


@admin.register(Poster)
class PosterAdmin(ReadOnlyAdmin):
    list_display = ("name", "template", "occasion", "updated_at")


@admin.register(PosterExport)
class PosterExportAdmin(ReadOnlyAdmin):
    list_display = ("poster", "export_no", "width", "height", "created_by", "created_at")
