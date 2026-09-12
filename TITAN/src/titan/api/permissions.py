"""Role → permission map for the dashboard API.

Kept as plain data (mirrored in the frontend's ``lib/permissions.ts``) so
adding a role later — receptionist, doctor, staff — is one new row here and
one in the frontend, with zero architecture changes.

Permission strings are hierarchical (``area:resource:action``); a trailing
``*`` grants everything underneath it (``business:*`` matches
``business:appointments:write``).
"""

from __future__ import annotations

PERMISSIONS: dict[str, set[str]] = {
    "super_admin": {"admin:*", "business:*"},
    "business_admin": {"business:*"},
    # Future roles (uncomment + mirror in frontend to enable):
    # "receptionist": {
    #     "business:overview",
    #     "business:appointments:*",
    #     "business:calls:read",
    #     "business:staff:read",
    # },
    # "doctor": {"business:overview", "business:appointments:read"},
}

BUSINESS_ROLES = frozenset(role for role in PERMISSIONS if role != "super_admin")


def has_permission(role: str, permission: str) -> bool:
    """True if ``role`` grants ``permission`` (wildcard-aware)."""
    granted = PERMISSIONS.get(role, set())
    if permission in granted:
        return True
    parts = permission.split(":")
    return any(":".join([*parts[:i], "*"]) in granted for i in range(len(parts)))
