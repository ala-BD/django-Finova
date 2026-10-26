from django import forms

from automation.models import RecurringExpense, ReminderRule


class RecurringExpenseForm(forms.ModelForm):
    class Meta:
        model = RecurringExpense
        fields = [
            "title",
            "description",
            "amount",
            "category",
            "frequency",
            "start_date",
            "next_run",
            "status",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "next_run": forms.DateInput(attrs={"type": "date"}),
        }


class ReminderRuleForm(forms.ModelForm):
    class Meta:
        model = ReminderRule
        fields = ["days_before", "channel", "enabled"]
