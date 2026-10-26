import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from analytics.datasource import get_user_expenses
from analytics.models import AnomalyFlag, Report, SavedFilter
from analytics.tests.helpers import fake_source

MEDIA_ROOT = tempfile.mkdtemp(prefix="finova-tests-")


@override_settings(MEDIA_ROOT=MEDIA_ROOT, EXCHANGE_RATE_API_URL="")
class AnalyticsViewTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        cache.clear()
        patcher = mock.patch("analytics.views.get_user_expenses", fake_source())
        patcher.start()
        self.addCleanup(patcher.stop)
        User = get_user_model()
        self.user = User.objects.create_user("zahra", password="un-mot-de-passe-fort")
        self.other = User.objects.create_user("intrus", password="un-mot-de-passe-fort")
        self.client.force_login(self.user)


class AccessTests(AnalyticsViewTestCase):
    def test_every_page_requires_login(self):
        saved = SavedFilter.objects.create(user=self.user, name="F")
        report = Report.objects.create(user=self.user, title="R")
        flag = _flag(self.user)
        urls = [
            reverse("analytics:dashboard"),
            reverse("analytics:summary"),
            reverse("analytics:filter_list"),
            reverse("analytics:filter_create"),
            reverse("analytics:filter_edit", args=[saved.pk]),
            reverse("analytics:filter_delete", args=[saved.pk]),
            reverse("analytics:filter_apply", args=[saved.pk]),
            reverse("analytics:report_list"),
            reverse("analytics:report_create"),
            reverse("analytics:report_detail", args=[report.pk]),
            reverse("analytics:report_delete", args=[report.pk]),
            reverse("analytics:report_download", args=[report.pk]),
            reverse("analytics:anomaly_list"),
        ]
        anonymous = Client()
        for url in urls:
            with self.subTest(url):
                response = anonymous.get(url)
                self.assertRedirects(response, f"{reverse('accounts:login')}?next={url}", fetch_redirect_response=False)
        for url in [reverse("analytics:anomaly_scan"), reverse("analytics:anomaly_review", args=[flag.pk])]:
            with self.subTest(url):
                self.assertEqual(anonymous.post(url).status_code, 302)

    def test_objects_of_another_user_are_not_found(self):
        saved = SavedFilter.objects.create(user=self.other, name="Secret", params={"category": "Loisirs"})
        report = Report.objects.create(user=self.other, title="Secret")
        flag = _flag(self.other)
        for url in [
            reverse("analytics:filter_edit", args=[saved.pk]),
            reverse("analytics:filter_delete", args=[saved.pk]),
            reverse("analytics:filter_apply", args=[saved.pk]),
            reverse("analytics:report_detail", args=[report.pk]),
            reverse("analytics:report_delete", args=[report.pk]),
            reverse("analytics:report_download", args=[report.pk]),
        ]:
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(reverse("analytics:filter_delete", args=[saved.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("analytics:anomaly_review", args=[flag.pk])).status_code, 404)
        self.assertTrue(SavedFilter.objects.filter(pk=saved.pk).exists())
        flag.refresh_from_db()
        self.assertFalse(flag.reviewed)

    def test_lists_only_show_own_objects(self):
        SavedFilter.objects.create(user=self.other, name="Filtre de l'intrus")
        Report.objects.create(user=self.other, title="Rapport de l'intrus")
        _flag(self.other, label="Dépense de l'intrus")
        self.assertNotContains(self.client.get(reverse("analytics:filter_list")), "intrus")
        self.assertNotContains(self.client.get(reverse("analytics:report_list")), "intrus")
        self.assertNotContains(self.client.get(reverse("analytics:anomaly_list") + "?statut=toutes"), "intrus")

    def test_post_endpoints_reject_get_and_require_csrf(self):
        self.assertEqual(self.client.get(reverse("analytics:anomaly_scan")).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(reverse("analytics:anomaly_scan")).status_code, 403)


class DashboardTests(AnalyticsViewTestCase):
    def test_dashboard_shows_kpis_charts_and_accessible_tables(self):
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["data"]["count"], 15)
        self.assertContains(response, 'id="chart-category"')
        self.assertContains(response, 'role="img"')
        self.assertContains(response, "Voir les données")
        self.assertContains(response, 'aria-busy="true"')
        self.assertContains(response, "Anomalie")
        self.assertNotContains(response, "Données de démonstration")

    def test_filters_and_currency_conversion(self):
        response = self.client.get(reverse("analytics:dashboard"), {"group": "2", "currency": "TND"})
        data = response.context["data"]
        self.assertEqual(data["count"], 1)
        self.assertEqual(str(data["total"]), "340.000")
        self.assertContains(response, "taux fixe indicatif")

    def test_invalid_filters_show_error_and_all_expenses(self):
        response = self.client.get(reverse("analytics:dashboard"), {"start": "2026-10-10", "end": "2026-10-01"})
        self.assertContains(response, "Certains filtres sont invalides")
        self.assertEqual(response.context["data"]["count"], 15)

    def test_empty_selection(self):
        response = self.client.get(reverse("analytics:dashboard"), {"min_amount": "100000"})
        self.assertContains(response, "Aucune dépense ne correspond")

    @mock.patch("analytics.datasource._load_real_expenses", side_effect=ImportError)
    def test_demo_notice(self, _):
        with mock.patch("analytics.views.get_user_expenses", get_user_expenses):
            response = self.client.get(reverse("analytics:dashboard"))
        self.assertContains(response, "Données de démonstration")

    @mock.patch("analytics.services.call_llm", side_effect=Exception("timeout"))
    def test_summary_endpoint_falls_back_without_llm(self, _):
        response = self.client.get(reverse("analytics:summary"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "template")
        self.assertIn("octobre 2026", payload["text"])
        self.assertIn("latency_ms", payload)


class SavedFilterTests(AnalyticsViewTestCase):
    def test_create_from_dashboard_query(self):
        url = reverse("analytics:filter_create") + "?category=Loisirs&min_amount=10"
        response = self.client.get(url)
        self.assertEqual(response.context["form"].initial, {"category": "Loisirs", "min_amount": "10"})
        response = self.client.post(url, {"name": "Sorties", "category": "Loisirs", "min_amount": "10"})
        self.assertRedirects(response, reverse("analytics:filter_list"))
        saved = SavedFilter.objects.get(user=self.user)
        self.assertEqual(saved.params, {"category": "Loisirs", "min_amount": "10"})

    def test_dashboard_save_button_sends_current_form_values(self):
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertContains(response, f'formaction="{reverse("analytics:filter_create")}"')

    def test_name_must_be_unique_per_user(self):
        SavedFilter.objects.create(user=self.user, name="Sorties")
        SavedFilter.objects.create(user=self.other, name="Courses")
        response = self.client.post(reverse("analytics:filter_create"), {"name": "sorties"})
        self.assertContains(response, "déjà un filtre portant ce nom")
        response = self.client.post(reverse("analytics:filter_create"), {"name": "Courses"})
        self.assertEqual(response.status_code, 302)

    def test_invalid_ranges_are_rejected(self):
        response = self.client.post(
            reverse("analytics:filter_create"),
            {"name": "X", "start": "2026-10-10", "end": "2026-10-01", "min_amount": "50", "max_amount": "10"},
        )
        self.assertContains(response, "La date de fin doit être postérieure")
        self.assertContains(response, "Le montant maximum doit être supérieur")
        self.assertContains(response, 'aria-invalid="true"')
        self.assertFalse(SavedFilter.objects.exists())

    def test_edit_keeps_own_name_and_updates_params(self):
        saved = SavedFilter.objects.create(user=self.user, name="Sorties", params={"category": "Loisirs"})
        url = reverse("analytics:filter_edit", args=[saved.pk])
        self.assertEqual(self.client.get(url).context["form"].initial["category"], "Loisirs")
        self.client.post(url, {"name": "Sorties", "member": "Mariem"})
        saved.refresh_from_db()
        self.assertEqual(saved.params, {"member": "Mariem"})

    def test_apply_redirects_to_dashboard_with_params(self):
        saved = SavedFilter.objects.create(user=self.user, name="Sorties", params={"category": "Loisirs"})
        response = self.client.get(reverse("analytics:filter_apply", args=[saved.pk]))
        self.assertRedirects(response, f"{reverse('analytics:dashboard')}?category=Loisirs&filtre={saved.pk}")
        dashboard = self.client.get(response.url)
        self.assertEqual(dashboard.context["data"]["count"], 2)
        self.assertContains(dashboard, "Filtre appliqué")

    def test_delete(self):
        saved = SavedFilter.objects.create(user=self.user, name="Sorties")
        self.assertContains(self.client.get(reverse("analytics:filter_delete", args=[saved.pk])), "Supprimer le filtre")
        self.client.post(reverse("analytics:filter_delete", args=[saved.pk]))
        self.assertFalse(SavedFilter.objects.exists())


@mock.patch("analytics.services.call_llm", side_effect=Exception("hors ligne"))
class ReportTests(AnalyticsViewTestCase):
    def test_csv_report(self, _):
        saved = SavedFilter.objects.create(user=self.user, name="Loisirs", params={"category": "Loisirs"})
        response = self.client.post(
            reverse("analytics:report_create"), {"title": "Sorties", "saved_filter": saved.pk, "format": "csv"}
        )
        report = Report.objects.get(user=self.user)
        self.assertRedirects(response, reverse("analytics:report_detail", args=[report.pk]))
        self.assertEqual(report.params, {"category": "Loisirs"})
        self.assertIn("octobre 2026", report.narrative_summary)

        download = self.client.get(reverse("analytics:report_download", args=[report.pk]))
        self.assertEqual(download["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn(f'attachment; filename="finova-rapport-{report.pk}.csv"', download["Content-Disposition"])
        lines = b"".join(download.streaming_content).decode("utf-8-sig").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(all("Loisirs" in line for line in lines[1:]))

    def test_pdf_report(self, _):
        self.client.post(reverse("analytics:report_create"), {"title": "Tout", "format": "pdf"})
        report = Report.objects.get(user=self.user)
        download = self.client.get(reverse("analytics:report_download", args=[report.pk]))
        self.assertEqual(download["Content-Type"], "application/pdf")
        self.assertTrue(b"".join(download.streaming_content).startswith(b"%PDF"))

    def test_missing_file_is_regenerated(self, _):
        report = Report.objects.create(user=self.user, title="Ancien", format="csv")
        download = self.client.get(reverse("analytics:report_download", args=[report.pk]))
        self.assertEqual(download.status_code, 200)
        report.refresh_from_db()
        self.assertTrue(report.file)

    def test_cannot_use_another_users_filter(self, _):
        foreign = SavedFilter.objects.create(user=self.other, name="Secret")
        response = self.client.post(
            reverse("analytics:report_create"), {"title": "Vol", "saved_filter": foreign.pk, "format": "csv"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Report.objects.exists())

    def test_delete_removes_file(self, _):
        self.client.post(reverse("analytics:report_create"), {"title": "Tout", "format": "csv"})
        report = Report.objects.get()
        storage, name = report.file.storage, report.file.name
        self.client.post(reverse("analytics:report_delete", args=[report.pk]))
        self.assertFalse(Report.objects.exists())
        self.assertFalse(storage.exists(name))


class AnomalyTests(AnalyticsViewTestCase):
    def test_scan_creates_flags_once(self):
        response = self.client.post(reverse("analytics:anomaly_scan"), follow=True)
        self.assertContains(response, "1 signalement(s), dont 1 nouveau(x)")
        self.client.post(reverse("analytics:anomaly_scan"))
        flag = AnomalyFlag.objects.get(user=self.user)
        self.assertEqual(flag.expense_ref, 30)
        self.assertEqual(flag.kind, AnomalyFlag.Kind.ANOMALY)

    def test_review_toggles_and_filters_tabs(self):
        flag = _flag(self.user, label="Marché · Colocation")
        self.client.post(reverse("analytics:anomaly_review", args=[flag.pk]))
        flag.refresh_from_db()
        self.assertTrue(flag.reviewed)
        self.assertEqual(list(self.client.get(reverse("analytics:anomaly_list")).context["flags"]), [])
        reviewed = self.client.get(reverse("analytics:anomaly_list") + "?statut=revues")
        self.assertEqual(list(reviewed.context["flags"]), [flag])
        self.assertContains(reviewed, "Remettre à revoir")
        self.client.post(reverse("analytics:anomaly_review", args=[flag.pk]))
        flag.refresh_from_db()
        self.assertFalse(flag.reviewed)


def _flag(user, label="Marché"):
    return AnomalyFlag.objects.create(
        user=user,
        expense_ref=30,
        expense_label=label,
        expense_amount="900",
        expense_date="2026-10-25",
        kind=AnomalyFlag.Kind.ANOMALY,
        score=12.5,
    )
