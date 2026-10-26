from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.ai import LLMError
from automation.models import (
    ChatMessage,
    Notification,
    RecurringExpense,
    RecurringOccurrence,
    ReminderDelivery,
    ReminderRule,
)
from automation.services import (
    AssistantRequestError,
    _parse_function_call,
    advance_date,
    answer_assistant_question,
    generate_recurring_expenses,
    send_due_reminders,
)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AutomationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="wassim",
            email="wassim@example.test",
            password="password",
        )
        self.other_user = get_user_model().objects.create_user(
            username="other",
            email="other@example.test",
            password="password",
        )
        self.recurring = RecurringExpense.objects.create(
            group_id=17,
            owner=self.user,
            title="Loyer",
            amount=Decimal("300.00"),
            category="logement",
            frequency=RecurringExpense.MONTHLY,
            start_date=date(2026, 1, 31),
            next_run=date(2026, 1, 31),
        )

    def test_amount_must_be_positive(self):
        expense = RecurringExpense(
            group_id=17,
            owner=self.user,
            title="Invalid",
            amount=Decimal("0"),
            start_date=date(2026, 1, 1),
            next_run=date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError):
            expense.full_clean()

    def test_next_run_cannot_precede_start_date(self):
        self.recurring.next_run = date(2025, 12, 31)
        with self.assertRaises(ValidationError):
            self.recurring.full_clean()

    def test_monthly_advance_clamps_end_of_month(self):
        self.assertEqual(advance_date(date(2026, 1, 31), "monthly"), date(2026, 2, 28))

    def test_yearly_advance_handles_leap_day(self):
        self.assertEqual(advance_date(date(2024, 2, 29), "yearly"), date(2025, 2, 28))

    def test_reminder_days_are_limited_to_thirty(self):
        rule = ReminderRule(
            recurring_expense=self.recurring,
            days_before=31,
            channel=ReminderRule.CHANNEL_NOTIFICATION,
        )
        with self.assertRaises(ValidationError):
            rule.full_clean()

    @patch("automation.services._create_expense_from_occurrence")
    def test_due_expense_is_generated_once_and_schedule_advances(self, create_expense):
        create_expense.return_value = SimpleNamespace(pk=42)

        first_count = generate_recurring_expenses(today=date(2026, 1, 31))
        second_count = generate_recurring_expenses(today=date(2026, 1, 31))

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(create_expense.call_count, 1)
        self.assertEqual(RecurringOccurrence.objects.count(), 1)
        self.assertEqual(
            RecurringOccurrence.objects.get().generated_expense_id,
            42,
        )
        self.recurring.refresh_from_db()
        self.assertEqual(self.recurring.next_run, date(2026, 2, 28))

    @patch("automation.services._create_expense_from_occurrence")
    def test_suspended_expense_is_not_generated(self, create_expense):
        self.recurring.status = RecurringExpense.SUSPENDED
        self.recurring.save(update_fields=["status"])

        generated = generate_recurring_expenses(today=date(2026, 1, 31))

        self.assertEqual(generated, 0)
        create_expense.assert_not_called()

    @patch("automation.services._create_expense_from_occurrence")
    def test_generation_error_rolls_back_occurrence_and_continues(self, create_expense):
        create_expense.side_effect = RuntimeError("M2 unavailable")

        generated = generate_recurring_expenses(today=date(2026, 1, 31))

        self.assertEqual(generated, 0)
        self.assertFalse(RecurringOccurrence.objects.exists())
        self.recurring.refresh_from_db()
        self.assertEqual(self.recurring.next_run, date(2026, 1, 31))

    @patch("automation.services.send_mail")
    def test_reminder_is_sent_once_per_due_date(self, send_mail_mock):
        rule = ReminderRule.objects.create(
            recurring_expense=self.recurring,
            days_before=0,
            channel=ReminderRule.CHANNEL_EMAIL,
        )

        first_count = send_due_reminders(today=date(2026, 1, 31))
        second_count = send_due_reminders(today=date(2026, 1, 31))

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        send_mail_mock.assert_called_once()
        self.assertEqual(ReminderDelivery.objects.filter(rule=rule).count(), 1)
        self.assertEqual(
            Notification.objects.filter(user=self.user, kind="recurring_reminder").count(),
            1,
        )

    def test_notification_listing_and_read_are_user_scoped(self):
        mine = Notification.objects.create(user=self.user, message="À lire")
        theirs = Notification.objects.create(user=self.other_user, message="Privée")
        self.client.force_login(self.user)

        response = self.client.get(reverse("automation:notifications"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "À lire")
        self.assertNotContains(response, "Privée")

        response = self.client.post(
            reverse("automation:notification_read", args=[theirs.pk])
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            reverse("automation:notification_read", args=[mine.pk])
        )
        self.assertEqual(response.status_code, 405)

        response = self.client.post(
            reverse("automation:notification_read", args=[mine.pk])
        )
        self.assertEqual(response.status_code, 302)
        mine.refresh_from_db()
        self.assertTrue(mine.is_read)

    def test_notification_page_requires_login(self):
        response = self.client.get(reverse("automation:notifications"))
        self.assertEqual(response.status_code, 302)

    @patch("automation.services._authorized_groups", return_value=[])
    @patch(
        "automation.services.call_llm",
        side_effect=[
            {"function": "list_expenses", "arguments": {"period": "month"}},
            "Aucune dépense ce mois-ci.",
        ],
    )
    def test_assistant_question_is_answered_and_saved(self, call_llm_mock, groups_mock):
        message = answer_assistant_question(self.user, "Quelles sont mes dépenses ?")

        self.assertEqual(message.user, self.user)
        self.assertEqual(message.answer, "Aucune dépense ce mois-ci.")
        self.assertEqual(ChatMessage.objects.filter(user=self.user).count(), 1)
        self.assertEqual(call_llm_mock.call_count, 2)

    def test_unknown_assistant_function_is_rejected(self):
        with self.assertRaises(AssistantRequestError):
            _parse_function_call({"function": "execute_sql", "arguments": {}})

    def test_assistant_rejects_invalid_function_arguments(self):
        with patch(
            "automation.services.call_llm",
            return_value={"function": "list_expenses", "arguments": {"period": "forever"}},
        ):
            with self.assertRaises(AssistantRequestError):
                answer_assistant_question(self.user, "Mes dépenses")

    @patch("automation.services._authorized_groups", return_value=[])
    @patch("automation.services.call_llm", side_effect=LLMError("API indisponible"))
    def test_assistant_has_a_french_fallback_when_llm_is_unavailable(
        self,
        call_llm_mock,
        groups_mock,
    ):
        message = answer_assistant_question(self.user, "Mes dépenses ce mois")

        self.assertIn("aucune donnée", message.answer)
        self.assertEqual(call_llm_mock.call_count, 2)

    def test_assistant_history_is_user_scoped(self):
        ChatMessage.objects.create(
            user=self.user,
            question="Question privée",
            answer="Réponse privée",
        )
        ChatMessage.objects.create(
            user=self.other_user,
            question="Autre question",
            answer="Autre réponse",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("automation:assistant"))

        self.assertContains(response, "Question privée")
        self.assertNotContains(response, "Autre question")
