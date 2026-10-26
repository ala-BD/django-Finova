from django.contrib import admin

from .models import Group, Invitation, Membership


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "currency", "default_split_rule", "is_archived", "created_at")
    list_filter = ("is_archived", "currency", "default_split_rule")
    search_fields = ("name",)
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "group", "role", "weight", "is_active")
    list_filter = ("role", "is_active")


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "group", "status", "expires_at", "created_at")
    list_filter = ("status",)
    readonly_fields = ("token",)
