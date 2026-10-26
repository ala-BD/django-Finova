from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "Profil Finova",
            {"fields": ("avatar", "preferred_currency", "income_profile", "monthly_income")},
        ),
    )
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "preferred_currency",
        "is_staff",
    )
