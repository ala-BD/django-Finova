from django.contrib import admin

from .models import AnomalyFlag, ExchangeRate, Report, SavedFilter


@admin.register(SavedFilter)
class SavedFilterAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "created_at")
    search_fields = ("name", "user__username")


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "format", "generated_at")
    list_filter = ("format",)
    search_fields = ("title", "user__username")


@admin.register(AnomalyFlag)
class AnomalyFlagAdmin(admin.ModelAdmin):
    list_display = ("expense_label", "kind", "score", "reviewed", "user", "created_at")
    list_filter = ("kind", "reviewed")


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("base", "target", "rate", "fetched_at")
