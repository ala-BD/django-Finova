from celery import shared_task

from automation.services import generate_recurring_expenses, send_due_reminders


@shared_task
def generate_recurring_expenses_task():
    return generate_recurring_expenses()


@shared_task
def send_pending_reminders_task():
    return send_due_reminders()
