"""M6 · Accès aux dépenses récurrentes depuis la page du groupe, et retour au groupe."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from groups.models import Group, Membership

User = get_user_model()


class RecurringNavigationTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user("wassim")
        self.group = Group.objects.create(name="Coloc")
        Membership.objects.create(group=self.group, user=self.member)
        self.client.force_login(self.member)

    def test_group_page_links_to_recurring_expenses(self):
        url = reverse("automation:recurring_list", kwargs={"gid": self.group.pk})
        page = self.client.get(reverse("groups:detail", args=[self.group.pk]))
        self.assertContains(page, f'href="{url}"')
        self.assertContains(page, "Dépenses récurrentes")

    def test_recurring_page_links_back_to_group(self):
        page = self.client.get(reverse("automation:recurring_list", kwargs={"gid": self.group.pk}))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'href="{reverse("groups:detail", args=[self.group.pk])}"')
