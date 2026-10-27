from .models import BusinessSettings


def business(request):
    """Expose business settings to every template (name/logo in the layout)."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {"business": BusinessSettings.load()}
