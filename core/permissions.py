"""Permission checks for views.

Today there is one owner account (a superuser, which passes every check). When
staff accounts are added, give them a Django group with just the permissions
they need — e.g. view products and record stock, but not change settings — and
these decorators enforce it. Failing a check shows a 403 page.
"""

from django.contrib.auth.decorators import permission_required


def perm_required(*perms: str):
    return permission_required(perms, raise_exception=True)
