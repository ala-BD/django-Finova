from django.contrib import admin

from .models import AIRequestLog


@admin.register(AIRequestLog)
class AIRequestLogAdmin(admin.ModelAdmin):
    list_display = ("module", "feature", "success", "latency_ms", "user", "created_at")
    list_filter = ("module", "success")
    search_fields = ("feature", "error_message")
    readonly_fields = ("user", "module", "feature", "latency_ms", "success", "error_message", "created_at")
