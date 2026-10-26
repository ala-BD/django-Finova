"""Rapport PDF des budgets et envoi par email."""

from datetime import date
from unittest import mock

from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse

from budgets.reports import build_budget_report, report_filename

from .factories import add_member, make_budget, make_category, make_expense, make_group, make_user

OCTOBER = date(2026, 10, 1)


class BuildReportTests(TestCase):
    def setUp(self):
        self.group = make_group(name="Coloc test")
        self.category = make_category(self.group, "Restaurants")
        make_budget(self.category, OCTOBER, limit="300")
        make_expense(self.category, "250", "2026-10-03")

    def test_pdf_is_valid_and_contains_figures(self):
        pdf = build_budget_report(self.group, OCTOBER)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1500)

    @mock.patch("budgets.services.timezone.localdate", return_value=date(2026, 10, 15))
    def test_current_month_report_includes_ai_analysis(self, _):
        with mock.patch("budgets.services.forecast_budget", wraps=__import__("budgets.services").services.forecast_budget) as forecast:
            build_budget_report(self.group, OCTOBER)
        forecast.assert_called_once()
        self.assertFalse(forecast.call_args.kwargs["save"])

    def test_empty_month_still_builds(self):
        self.assertTrue(build_budget_report(self.group, date(2020, 1, 1)).startswith(b"%PDF"))

    def test_filename(self):
        self.assertEqual(report_filename(self.group, OCTOBER), f"finova-budgets-groupe{self.group.pk}-2026-10.pdf")


class ReportViewsTests(TestCase):
    def setUp(self):
        self.admin = make_user("linda")
        self.group = make_group(admin=self.admin)
        self.member = add_member(self.group, make_user("yahya")).user
        category = make_category(self.group, "Restaurants")
        make_budget(category, OCTOBER)
        self.pdf_url = reverse("budgets:report_pdf", kwargs={"gid": self.group.pk}) + "?month=2026-10"
        self.email_url = reverse("budgets:report_email", kwargs={"gid": self.group.pk})

    def test_member_downloads_pdf(self):
        self.client.force_login(self.member)
        response = self.client.get(self.pdf_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn("2026-10.pdf", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_member_receives_pdf_by_email(self):
        self.client.force_login(self.member)
        response = self.client.post(self.email_url, {"month": "2026-10"}, follow=True)
        self.assertContains(response, "Rapport envoyé à yahya@example.com")
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["yahya@example.com"])
        self.assertIn("octobre 2026", message.subject)
        name, content, mimetype = message.attachments[0]
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF"))

    def test_pdf_sent_to_typed_address(self):
        self.client.force_login(self.member)
        response = self.client.post(self.email_url, {"month": "2026-10", "email": "  linda.perso@gmail.com "}, follow=True)
        self.assertContains(response, "Rapport envoyé à linda.perso@gmail.com")
        self.assertEqual(mail.outbox[0].to, ["linda.perso@gmail.com"])

    def test_invalid_or_missing_address_gets_error(self):
        self.client.force_login(self.member)
        response = self.client.post(self.email_url, {"email": "pas-un-email"}, follow=True)
        self.assertContains(response, "Saisissez une adresse email valide")
        self.member.email = ""
        self.member.save()
        response = self.client.post(self.email_url, follow=True)
        self.assertContains(response, "Saisissez une adresse email valide")
        self.assertEqual(len(mail.outbox), 0)

    def test_email_requires_post_and_csrf(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.email_url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.member)
        self.assertEqual(client.post(self.email_url).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_external_redirect_is_ignored(self):
        self.client.force_login(self.member)
        response = self.client.post(self.email_url, {"next": "https://evil.example/"})
        self.assertEqual(response["Location"], reverse("budgets:list", kwargs={"gid": self.group.pk}))

    def test_outsider_refused(self):
        self.client.force_login(make_user())
        self.assertEqual(self.client.get(self.pdf_url).status_code, 403)
        self.assertEqual(self.client.post(self.email_url).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_buttons_shown_on_budget_page(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("budgets:list", kwargs={"gid": self.group.pk}))
        self.assertContains(response, "Télécharger le PDF")
        self.assertContains(response, "Recevoir par email")
