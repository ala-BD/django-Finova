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

    def test_password_is_hashed_never_stored_in_clear(self):
        user = get_user_model().objects.create_user(username="linda", password="un-mot-de-passe-fort")
        self.assertNotIn("un-mot-de-passe-fort", user.password)
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))

    def test_profile_defaults_and_monthly_income(self):
        from decimal import Decimal

        user = get_user_model().objects.create_user(
            username="linda", password="un-mot-de-passe-fort", monthly_income=Decimal("1800.500")
        )
        self.assertEqual(user.income_profile, "active")
        self.assertEqual(user.monthly_income, Decimal("1800.500"))
        self.assertEqual(str(user), "linda")

    def test_get_groups_returns_active_memberships_of_unarchived_groups(self):
        from groups.models import Group, Membership

        User = get_user_model()
        linda = User.objects.create_user(username="linda", password="un-mot-de-passe-fort")
        owner = User.objects.create_user(username="ala", password="un-mot-de-passe-fort")
        active = Group.objects.create(name="Coloc", created_by=owner)
        archived = Group.objects.create(name="Ancien voyage", created_by=owner, is_archived=True)
        left = Group.objects.create(name="Club quitté", created_by=owner)
        Group.objects.create(name="Pas membre", created_by=owner)
        Membership.objects.create(user=linda, group=active)
        Membership.objects.create(user=linda, group=archived)
        Membership.objects.create(user=linda, group=left, is_active=False)
        self.assertEqual(list(linda.get_groups()), [active])


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


class RegisterViewTests(TestCase):
    def setUp(self):
        self.register_url = reverse("accounts:register")
        self.valid_payload = {
            "first_name": "Yahya",
            "last_name": "Test",
            "username": "yahya",
            "email": "yahya@example.com",
            "password1": "SecretP@ssw0rd2026!",
            "password2": "SecretP@ssw0rd2026!",
        }

    def test_register_page_renders_accessible_form(self):
        """En GET : le formulaire est présent, accessible, avec labels et CSRF.
        L'aria-describedby n'apparaît qu'après validation (voir le test POST ci-dessous).
        """
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<form")
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, 'for="id_email"')
        self.assertContains(response, 'for="id_password1"')
        self.assertContains(response, 'autocomplete="email"')
        self.assertContains(response, 'autocomplete="new-password"')

    def test_register_invalid_post_injects_aria_attributes(self):
        """En POST invalide : full_clean() injecte aria-invalid et aria-describedby."""
        payload = {**self.valid_payload, "email": "pas-un-email"}
        response = self.client.post(self.register_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'aria-describedby="id_email-error"')

    def test_register_success_creates_user_and_redirects(self):
        response = self.client.post(self.register_url, self.valid_payload)
        self.assertRedirects(response, reverse("accounts:login"))
        User = get_user_model()
        user = User.objects.filter(username=self.valid_payload["username"]).first()
        self.assertIsNotNone(user)
        self.assertNotEqual(user.password, self.valid_payload["password1"])
        self.assertTrue(user.check_password(self.valid_payload["password1"]))

    def test_register_duplicate_email_rejected(self):
        User = get_user_model()
        User.objects.create_user(
            username="existing_user",
            email="yahya@example.com",
            password="ExistingPassword!123",
        )
        initial_count = User.objects.count()
        payload = self.valid_payload.copy()
        payload["email"] = "yahya@example.com"
        response = self.client.post(self.register_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "email",
            "Cette adresse e-mail est déjà utilisée.",
        )
        self.assertEqual(User.objects.count(), initial_count)

    def test_register_password_mismatch_rejected(self):
        User = get_user_model()
        initial_count = User.objects.count()
        payload = self.valid_payload.copy()
        payload["password2"] = "DifferentPassword!456"
        response = self.client.post(self.register_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), initial_count)

    def test_register_weak_password_rejected(self):
        User = get_user_model()
        initial_count = User.objects.count()
        payload = self.valid_payload.copy()
        payload["password1"] = "12345678"
        payload["password2"] = "12345678"
        response = self.client.post(self.register_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), initial_count)

    def test_register_redirects_authenticated_user(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="logged_in_user",
            email="loggedin@example.com",
            password="StrongPassword!123",
        )
        self.client.force_login(user)
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("accounts:login"), target_status_code=302)



