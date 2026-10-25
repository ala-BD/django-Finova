from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse


class UserModelTests(TestCase):
    def test_auth_user_model(self):
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.User")

    def test_create_user(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="ala",
            email="ala@example.com",
            password="un-mot-de-passe-fort",
        )
        self.assertEqual(user.preferred_currency, "TND")
        self.assertTrue(user.check_password("un-mot-de-passe-fort"))


class LoginLogoutTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ala",
            email="ala@example.com",
            password="un-mot-de-passe-fort",
            first_name="Ala",
        )
        self.login_url = reverse("accounts:login")
        self.logout_url = reverse("accounts:logout")

    def test_login_page_is_accessible(self):
        response = self.client.get(self.login_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'for="id_username"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, "Aller au contenu")

    def test_login_success_redirects_and_shows_user(self):
        response = self.client.post(
            self.login_url,
            {"username": "ala", "password": "un-mot-de-passe-fort"},
        )
        self.assertRedirects(response, reverse("home"))
        follow = self.client.get(reverse("home"))
        self.assertContains(follow, "Ala")
        self.assertContains(follow, "Déconnexion")
        self.assertContains(follow, "csrfmiddlewaretoken")

    def test_login_honours_next_parameter(self):
        response = self.client.post(
            f"{self.login_url}?next=/groups/",
            {"username": "ala", "password": "un-mot-de-passe-fort", "next": "/groups/"},
        )
        self.assertRedirects(response, "/groups/")

    def test_login_failure_is_generic(self):
        response = self.client.post(
            self.login_url,
            {"username": "inconnu", "password": "mauvais"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Identifiant ou mot de passe incorrect.")
        self.assertNotContains(response, "n’existe pas")
        self.assertNotContains(response, "does not exist")
        self.assertContains(response, 'aria-live="assertive"')

    def test_logout_get_is_rejected(self):
        self.client.force_login(self.user)
        response = self.client.get(self.logout_url)
        self.assertEqual(response.status_code, 405)

    def test_logout_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(self.logout_url)
        self.assertEqual(response.status_code, 403)

    def test_logout_post_with_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        home = client.get(reverse("home"))
        token = home.cookies["csrftoken"].value
        response = client.post(
            self.logout_url,
            {"csrfmiddlewaretoken": token},
            follow=True,
        )
        self.assertRedirects(response, self.login_url, target_status_code=200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertContains(response, "Vous êtes déconnecté.")
