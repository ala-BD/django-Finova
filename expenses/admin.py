from django.contrib import admin

from .models import Expense, ExpenseItem, ExpenseShare, ItemClaim


class ExpenseItemInline(admin.TabularInline):
    model = ExpenseItem
    extra = 0


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("title", "group", "category", "amount", "currency", "expense_date", "source")
    list_filter = ("group", "category", "source")
    search_fields = ("title",)
    date_hierarchy = "expense_date"
    inlines = [ExpenseItemInline]


admin.site.register(ItemClaim)
admin.site.register(ExpenseShare)
