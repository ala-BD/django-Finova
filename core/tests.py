from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import path

from core.rounding import largest_remainder_rounding
from groups.models import Group


def boom(request):
    raise RuntimeError("erreur volontaire pour tester la page 500")


# URLconf minimale utilisée par ErrorPagesTests.test_500 (ROOT_URLCONF="core.tests")
urlpatterns = [path("boom/", boom)]
handler500 = "core.views.error_500"


class RoundingTests(TestCase):
    def test_sum_equals_total(self):
        parts = {"a": Decimal("15.001"), "b": Decimal("15.001"), "c": Decimal("15.001")}
        rounded = largest_remainder_rounding(parts, Decimal("45.003"), places=3)
        self.assertEqual(sum(rounded.values()), Decimal("45.003"))


@override_settings(DEBUG=False)
class ErrorPagesTests(TestCase):
    """Pages 403/404/500 personnalisées, testées avec DEBUG=False (Imem)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="imem")

    def test_404_custom_page(self):
        response = self.client.get("/cette-page-n-existe-pas/")
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "404.html")
        self.assertContains(response, "Page introuvable", status_code=404)
        self.assertContains(response, "/cette-page-n-existe-pas/", status_code=404)
        self.assertContains(response, 'href="#contenu"', status_code=404)  # gabarit commun accessible

    def test_404_for_missing_object(self):
        self.client.force_login(self.user)
        response = self.client.get("/groups/999999/balances/")
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "404.html")

    def test_403_custom_page_for_non_member(self):
        group = Group.objects.create(name="Groupe privé")
        self.client.force_login(self.user)
        response = self.client.get(f"/groups/{group.pk}/balances/")
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "403.html")
        self.assertContains(response, "Accès refusé", status_code=403)

    @override_settings(ROOT_URLCONF="core.tests")
    def test_500_custom_page(self):
        client = Client(raise_request_exception=False)
        response = client.get("/boom/")
        self.assertEqual(response.status_code, 500)
        self.assertIn("Une erreur est survenue", response.content.decode())

    def test_csrf_failure_page(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post("/accounts/logout/")
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "403_csrf.html")
        self.assertContains(response, "Le formulaire a expiré", status_code=403)
