from django.core.management.base import BaseCommand

from automation.services import send_due_reminders


class Command(BaseCommand):
    help = "Send due reminders for active recurring expenses."

    def handle(self, *args, **options):
        count = send_due_reminders()
        self.stdout.write(self.style.SUCCESS(f"Sent {count} reminder(s)."))
