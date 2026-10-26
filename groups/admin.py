from django.contrib import admin
from .models import Group, Invitation, Membership


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 1


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "currency", "default_split_rule", "created_by", "is_archived", "created_at")
    list_filter = ("currency", "default_split_rule", "is_archived")
    search_fields = ("name", "created_by__username")
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "group", "role", "weight", "is_active", "joined_at")
    list_filter = ("role", "is_active", "group")
    search_fields = ("user__username", "group__name")


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "group", "status", "expires_at", "invited_by", "created_at")
    list_filter = ("status", "group")
