# ============================================================
# services/admin/registry.py
# Capability registry — the single source of truth for what
# admin capabilities can exist.
#
# Immutable. Ships with the app. Cannot be modified at runtime.
# New capabilities added here appear on the next boot.
# ============================================================

from typing import NamedTuple, Optional


class Capability(NamedTuple):
    key: str
    group: str
    label: str
    description: str
    default_for_admin: bool
    reserved: bool


# ============================================================
# GROUP NAMES — fixed list, used for UI ordering
# ============================================================

GROUPS = [
    'Overview',
    'Users',
    'Questions',
    'Groups',
    'PDFs',
    'Upgrades',
    'Reports',
    'Diagnostics',
    'Communication',
    'Governance',
]


# ============================================================
# THE REGISTRY — 60 capabilities across 10 groups
# ============================================================

CAPABILITY_REGISTRY: tuple[Capability, ...] = (

    # ---------- Overview ----------
    Capability(
        key='dashboard.view',
        group='Overview',
        label='View admin dashboard',
        description='Access the admin home page and its summary tiles.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='activity.view',
        group='Overview',
        label='View activity log',
        description='Read the platform activity log.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='platform.view',
        group='Overview',
        label='View platform monitor',
        description='Access the live platform monitoring page.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='search.use',
        group='Overview',
        label='Use global search',
        description='Search across users, questions, PDFs, and groups.',
        default_for_admin=True,
        reserved=False,
    ),

    # ---------- Users ----------
    Capability(
        key='users.view',
        group='Users',
        label='View user list and profiles',
        description='Browse and search the user directory.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='users.notify',
        group='Users',
        label='Send notifications to a user',
        description='Deliver an in-app message to a single user.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='users.set_tier',
        group='Users',
        label="Change a user's tier",
        description='Promote or demote between free, premium, and pro.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='users.toggle_admin',
        group='Users',
        label='Grant or revoke admin access',
        description='Add or remove the is_admin flag from a user.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='users.reset_password',
        group='Users',
        label="Reset a user's password",
        description='Set a new password for another user.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='users.force_logout',
        group='Users',
        label='Force logout a user',
        description='Invalidate every active session for a user.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='users.impersonate',
        group='Users',
        label='View platform as another user',
        description='Session-switch into another account for support.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='users.delete',
        group='Users',
        label='Delete a user',
        description='Soft-delete a user; restorable from Deleted Users.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='users.restore',
        group='Users',
        label='Restore a deleted user',
        description='Recreate a user from the deleted users table.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='users.bulk',
        group='Users',
        label='Bulk actions on users',
        description='Select multiple users and apply an action.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='users.export',
        group='Users',
        label='Export users as CSV',
        description='Download the filtered user list.',
        default_for_admin=False,
        reserved=False,
    ),

    # ---------- Questions ----------
    Capability(
        key='questions.view',
        group='Questions',
        label='View questions',
        description='Browse and search the question bank.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='questions.create',
        group='Questions',
        label='Create a new question',
        description='Add a single question through the editor.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='questions.edit',
        group='Questions',
        label='Edit an existing question',
        description='Modify question text, options, or metadata.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='questions.bulk_import',
        group='Questions',
        label='Bulk import questions',
        description='Upload JSON to insert questions in batch.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='questions.archive',
        group='Questions',
        label='Archive a question',
        description='Hide a question from the active bank.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='questions.delete',
        group='Questions',
        label='Permanently delete a question',
        description='Remove a question and its interactions.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='questions.export',
        group='Questions',
        label='Export questions as JSON',
        description='Download a filtered batch of questions.',
        default_for_admin=False,
        reserved=False,
    ),

    # ---------- Groups ----------
    Capability(
        key='groups.view',
        group='Groups',
        label='View groups',
        description='Browse the study group directory.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='groups.create',
        group='Groups',
        label='Create a group',
        description='Add a new WhatsApp or Telegram group.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='groups.edit',
        group='Groups',
        label='Edit a group',
        description='Change a group name, link, or metadata.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='groups.delete',
        group='Groups',
        label='Delete a group',
        description='Remove a group permanently.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='groups.feature',
        group='Groups',
        label='Feature or unfeature a group',
        description='Highlight a group in the featured section.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='groups.bulk',
        group='Groups',
        label='Bulk actions on groups',
        description='Select multiple groups and apply an action.',
        default_for_admin=False,
        reserved=False,
    ),

    # ---------- PDFs ----------
    Capability(
        key='pdfs.view',
        group='PDFs',
        label='View PDF library',
        description='Browse the main and bot PDF libraries.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='pdfs.intake',
        group='PDFs',
        label='Process pending intake',
        description='Fulfil PDFs submitted through the bot.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='pdfs.edit',
        group='PDFs',
        label='Edit PDF metadata',
        description='Change title, subject, curriculum, and tags.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='pdfs.publish',
        group='PDFs',
        label='Publish bot PDF to main library',
        description='Copy a fulfilled PDF to the public library.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='pdfs.delete',
        group='PDFs',
        label='Delete a PDF',
        description='Remove a PDF from its library.',
        default_for_admin=False,
        reserved=False,
    ),

    # ---------- Upgrades ----------
    Capability(
        key='upgrades.view',
        group='Upgrades',
        label='View upgrade requests',
        description='See user-submitted tier upgrade requests.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='upgrades.approve',
        group='Upgrades',
        label='Approve an upgrade request',
        description='Grant the requested tier to a user.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='upgrades.reject',
        group='Upgrades',
        label='Reject an upgrade request',
        description='Decline a request with an optional reason.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='upgrades.bulk',
        group='Upgrades',
        label='Bulk actions on upgrade requests',
        description='Approve, reject, or delete multiple requests.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='upgrades.export',
        group='Upgrades',
        label='Export upgrade requests as CSV',
        description='Download the filtered request list.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='discounts.view',
        group='Upgrades',
        label='View discount codes',
        description='Browse the discount code catalog.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='discounts.create',
        group='Upgrades',
        label='Create a discount code',
        description='Add a new percentage or fixed-amount code.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='discounts.edit',
        group='Upgrades',
        label='Edit a discount code',
        description='Change value, expiry, or applicability.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='discounts.delete',
        group='Upgrades',
        label='Delete a discount code',
        description='Remove a code permanently.',
        default_for_admin=False,
        reserved=False,
    ),

    # ---------- Reports ----------
    Capability(
        key='reports.view',
        group='Reports',
        label='View reported questions',
        description='See questions flagged by users.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='reports.resolve',
        group='Reports',
        label='Resolve a report',
        description='Mark a report resolved with an optional reply.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='reports.dismiss',
        group='Reports',
        label='Dismiss a report',
        description='Mark a report dismissed without action.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='reports.bulk',
        group='Reports',
        label='Bulk actions on reports',
        description='Resolve or dismiss multiple reports at once.',
        default_for_admin=False,
        reserved=False,
    ),

    # ---------- Diagnostics ----------
    Capability(
        key='errors.view',
        group='Diagnostics',
        label='View error log',
        description='Read the platform error dashboard.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='errors.resolve',
        group='Diagnostics',
        label='Resolve an error',
        description='Mark an error resolved with a note.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='errors.dismiss',
        group='Diagnostics',
        label='Dismiss an error',
        description='Hide an error without resolving it.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='errors.clear',
        group='Diagnostics',
        label='Clear resolved errors',
        description='Permanently delete resolved error records.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='backups.view',
        group='Diagnostics',
        label='View backup dashboard',
        description='See backup history, sizes, and integrity.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='backups.create',
        group='Diagnostics',
        label='Create a new backup',
        description='Manually trigger a database snapshot.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='backups.restore',
        group='Diagnostics',
        label='Restore from a backup',
        description='Revert the database to a snapshot.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='backups.delete',
        group='Diagnostics',
        label='Delete a backup',
        description='Permanently remove a backup file.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='backups.settings',
        group='Diagnostics',
        label='Configure backup retention',
        description='Change how many backups of each type are kept.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='logs.view',
        group='Diagnostics',
        label='View log files',
        description='Tail application, worker, and task logs.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='system.info',
        group='Diagnostics',
        label='View system information',
        description='Uptime, memory, CPU, disk usage.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='system.config',
        group='Diagnostics',
        label='View masked configuration',
        description='Read Config values with secrets masked.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='cache.view',
        group='Diagnostics',
        label='View cache statistics',
        description='Read in-memory cache size and hit ratio.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='cache.clear',
        group='Diagnostics',
        label='Clear the in-memory cache',
        description='Wipe the process-local cache.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='sessions.view',
        group='Diagnostics',
        label='View active sessions',
        description='See session count and disk usage.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='sessions.manage',
        group='Diagnostics',
        label='Force logout sessions',
        description='Invalidate sessions for users or globally.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='tasks.view',
        group='Diagnostics',
        label='View task run history',
        description='See the daily task runner history.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='tasks.trigger',
        group='Diagnostics',
        label='Manually trigger a task',
        description='Run a daily task on demand.',
        default_for_admin=False,
        reserved=False,
    ),

    # ---------- Communication ----------
    Capability(
        key='announcements.send',
        group='Communication',
        label='Send to all users',
        description='Broadcast an in-app announcement.',
        default_for_admin=True,
        reserved=False,
    ),
    Capability(
        key='platform.broadcast',
        group='Communication',
        label='Broadcast to all admins',
        description='Send a notification to every admin.',
        default_for_admin=False,
        reserved=False,
    ),

    # ---------- Governance ----------
    Capability(
        key='audit.view',
        group='Governance',
        label='View the unified audit log',
        description='Read every admin action across the platform.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='entitlements.read',
        group='Governance',
        label='View entitlement policies',
        description='See what each tier is allowed to do.',
        default_for_admin=False,
        reserved=False,
    ),
    Capability(
        key='entitlements.write',
        group='Governance',
        label='Edit entitlement policies',
        description='Change what each tier is allowed to do.',
        default_for_admin=False,
        reserved=True,
    ),
    Capability(
        key='admins.manage',
        group='Governance',
        label='Manage admin capabilities',
        description='Configure global defaults and per-admin overrides.',
        default_for_admin=False,
        reserved=True,
    ),
)


# ============================================================
# LOOKUP TABLES — computed once at import time
# ============================================================

_BY_KEY = {c.key: c for c in CAPABILITY_REGISTRY}

REGISTRY_KEYS: frozenset[str] = frozenset(_BY_KEY.keys())

RESERVED_KEYS: frozenset[str] = frozenset(
    c.key for c in CAPABILITY_REGISTRY if c.reserved
)

DEFAULT_ENABLED_KEYS: frozenset[str] = frozenset(
    c.key for c in CAPABILITY_REGISTRY if c.default_for_admin and not c.reserved
)

WRITABLE_KEYS: frozenset[str] = frozenset(
    c.key for c in CAPABILITY_REGISTRY if not c.reserved
)


# ============================================================
# PUBLIC HELPERS
# ============================================================

def get_capability(key: str) -> Optional[Capability]:
    """Return a Capability by key, or None if unknown."""
    return _BY_KEY.get(key)


def get_capabilities_by_group() -> dict[str, list[Capability]]:
    """Return {group_name: [capability, ...]} preserving registry order."""
    result: dict[str, list[Capability]] = {g: [] for g in GROUPS}
    for cap in CAPABILITY_REGISTRY:
        if cap.group in result:
            result[cap.group].append(cap)
    return result


def list_writable_capabilities() -> list[Capability]:
    """All capabilities that can be toggled (excludes reserved)."""
    return [c for c in CAPABILITY_REGISTRY if not c.reserved]


def is_reserved(key: str) -> bool:
    return key in RESERVED_KEYS


def is_known(key: str) -> bool:
    return key in REGISTRY_KEYS