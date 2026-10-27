from django.contrib.auth import views as auth_views
from django.urls import path

app_name = "accounts"

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="accounts/login.html", redirect_authenticated_user=True),
        name="login",
    ),
    # Django's LogoutView only accepts POST, so a link or prefetch can't sign you out.
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
