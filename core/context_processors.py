from products.models import Product

from .models import BusinessSettings


def business(request):
    """Business settings (name/logo in the layout) and the low-stock count for
    the sidebar badge, for signed-in users only."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    tracked = Product.objects.filter(is_active=True, track_stock=True)
    attention = (Product.objects.low_stock() | tracked.filter(stock_qty__lte=0)).distinct().count()
    return {"business": BusinessSettings.load(), "low_stock_count": attention}
