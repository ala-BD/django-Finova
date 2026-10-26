from django.contrib import admin
from .models import Expense, ExpenseItem, ExpenseShare, ItemClaim


class ExpenseItemInline(admin.TabularInline):
    model = ExpenseItem
    extra = 1


class ExpenseShareInline(admin.TabularInline):
    model = ExpenseShare
    extra = 0
    readonly_fields = ("member", "amount_due")


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "group",
        "payer",
        "amount",
        "currency",
        "expense_date",
        "split_rule",
        "source",
        "created_at",
    )
    list_filter = ("split_rule", "source", "expense_date", "group")
    search_fields = ("title", "payer__user__username", "group__name")
    inlines = [ExpenseItemInline, ExpenseShareInline]


class ItemClaimInline(admin.TabularInline):
    model = ItemClaim
    extra = 1


@admin.register(ExpenseItem)
class ExpenseItemAdmin(admin.ModelAdmin):
    list_display = ("label", "expense", "unit_price", "quantity", "total")
    search_fields = ("label", "expense__title")
    inlines = [ItemClaimInline]


@admin.register(ExpenseShare)
class ExpenseShareAdmin(admin.ModelAdmin):
    list_display = ("expense", "member", "amount_due")
    list_filter = ("expense__group",)


@admin.register(ItemClaim)
class ItemClaimAdmin(admin.ModelAdmin):
    list_display = ("item", "member", "portion")
