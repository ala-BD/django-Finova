from django.contrib import admin

from .models import Installment, RepaymentPlan, RiskPrediction, Settlement


@admin.register(Settlement)
class SettlementAdmin(admin.ModelAdmin):
    list_display = ("group", "payer", "receiver", "amount", "status", "created_at")
    list_filter = ("status",)


class InstallmentInline(admin.TabularInline):
    model = Installment
    extra = 0


@admin.register(RepaymentPlan)
class RepaymentPlanAdmin(admin.ModelAdmin):
    list_display = ("settlement", "installments_count", "frequency", "start_date", "status")
    inlines = [InstallmentInline]


@admin.register(RiskPrediction)
class RiskPredictionAdmin(admin.ModelAdmin):
    list_display = ("plan", "probability", "label", "model_version", "latency_ms", "created_at")
