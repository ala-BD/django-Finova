"""Mixins de permissions. Zahra (A5) complète les tests et la logique finale."""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404


class GroupMemberRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    group_url_kwarg = "gid"
    group_object = None

    def get_group(self):
        from groups.models import Group

        if self.group_object is None:
            self.group_object = get_object_or_404(
                Group, pk=self.kwargs.get(self.group_url_kwarg)
            )
        return self.group_object

    def test_func(self):
        from groups.services import is_member

        return is_member(self.request.user, self.get_group())


class GroupAdminRequiredMixin(GroupMemberRequiredMixin):
    def test_func(self):
        from groups.services import is_admin

        return is_admin(self.request.user, self.get_group())


def require_group_member(user, group):
    from groups.services import is_member

    if not is_member(user, group):
        raise PermissionDenied


def require_group_admin(user, group):
    from groups.services import is_admin

    if not is_admin(user, group):
        raise PermissionDenied
