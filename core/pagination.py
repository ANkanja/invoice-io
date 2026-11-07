from django.core.paginator import Paginator


def paginate(request, queryset, per_page: int = 25):
    """Return a page of results; bad or out-of-range ?page= values fall back safely."""
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))
