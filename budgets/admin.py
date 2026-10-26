from django.contrib import admin

from .models import Budget, BudgetForecast, Category


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "group", "color", "icon")
    list_filter = ("group",)
    search_fields = ("name",)


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ("category", "group", "month", "limit_amount", "carry_over", "alert_80_sent", "alert_100_sent")
    list_filter = ("group", "month", "carry_over")


@admin.register(BudgetForecast)
class BudgetForecastAdmin(admin.ModelAdmin):
    list_display = ("budget", "predicted_total", "baseline_total", "method", "created_at")
    readonly_fields = ("created_at",)
