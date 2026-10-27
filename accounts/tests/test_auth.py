from django.test import TestCase
from django.urls import reverse

from core.testing import TEST_PASSWORD, make_user


class LoginRequiredTests(TestCase):
    """Every private page redirects anonymous visitors to the login page."""

    PRIVATE_URLS = [
        reverse("core:dashboard"),
        reverse("core:settings"),
        "/media/branding/anything.png",
    ]

    def test_anonymous_redirected_to_login(self):
        for url in self.PRIVATE_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("accounts:login")))

    def test_settings_post_rejected_for_anonymous(self):
        response = self.client.post(reverse("core:settings"), {"name": "Hijack"})
        self.assertEqual(response.status_code, 302)
        from core.models import BusinessSettings
        self.assertEqual(BusinessSettings.load().name, "")

    def test_login_page_is_public(self):
        self.assertEqual(self.client.get(reverse("accounts:login")).status_code, 200)

    def test_admin_login_is_reachable(self):
        response = self.client.get("/admin/login/")
        self.assertEqual(response.status_code, 200)


class LoginFlowTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_login_then_dashboard(self):
        response = self.client.post(reverse("accounts:login"), {"username": "owner", "password": TEST_PASSWORD})
        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)

    def test_wrong_password(self):
        response = self.client.post(reverse("accounts:login"), {"username": "owner", "password": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "correct username and password")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_next_parameter_is_respected_but_not_offsite(self):
        url = reverse("accounts:login") + "?next=https://evil.example.com/"
        response = self.client.post(url, {"username": "owner", "password": TEST_PASSWORD,
                                          "next": "https://evil.example.com/"})
        self.assertRedirects(response, reverse("core:dashboard"))

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 302)

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(reverse("accounts:login"), {"username": "owner", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 200)

    def test_csrf_enforced_on_login(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("accounts:login"), {"username": "owner", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 403)

    def test_password_is_hashed(self):
        self.assertNotIn(TEST_PASSWORD, self.user.password)
        self.assertTrue(self.user.password.startswith(("pbkdf2_", "argon2", "scrypt")))
