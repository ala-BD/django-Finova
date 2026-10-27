from django.contrib import admin

from .models import ChatMessage, Notification, RecurringExpense, ReminderRule


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "message", "is_read", "created_at")
    list_filter = ("kind", "is_read")


admin.site.register(RecurringExpense)
admin.site.register(ReminderRule)
admin.site.register(ChatMessage)
