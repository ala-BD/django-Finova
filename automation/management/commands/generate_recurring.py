from django.core.management.base import BaseCommand

from automation.services import generate_recurring_expenses


class Command(BaseCommand):
    help = "Generate due recurring expenses for groups."

    def handle(self, *args, **options):
        count = generate_recurring_expenses()
        self.stdout.write(self.style.SUCCESS(f"Generated {count} recurring expense(s)."))
