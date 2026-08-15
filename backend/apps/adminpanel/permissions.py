"""Access control for the operator console.

The public tender API stays open and anonymous. Everything under
``/api/admin/`` is the operator console instead: it exposes raw upstream HTML,
sync internals and task triggers, so it is restricted to active **staff**
accounts — the same accounts the Django admin uses.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.core.i18n import translate


class IsStaffUser(BasePermission):
    """Allow only authenticated, active users flagged ``is_staff``."""

    # DRF passes `code` through to the raised PermissionDenied, which is what
    # the error envelope localises. `message` stays as the English fallback for
    # logs and the browsable API.
    code = "staff_required"
    message = translate("staff_required", "en")

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.is_staff
        )
