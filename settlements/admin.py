from django.contrib import admin

from .models import Installment, RepaymentPlan, RiskPrediction, Settlement


@admin.register(Settlement)
class SettlementAdmin(admin.ModelAdmin):
    list_display = ("payer", "receiver", "amount", "status", "created_at")
    list_filter = ("status",)


admin.site.register(RepaymentPlan)
admin.site.register(Installment)
admin.site.register(RiskPrediction)
