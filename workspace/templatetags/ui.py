"""Small accessible presentation helpers. Introduced: 2026-11-18; revisions: Git."""

from django import template
from django.utils.html import format_html
from django.utils.translation import get_language
from django.utils.translation import gettext as _

register = template.Library()


@register.simple_tag
def icon(name, size=20):
    return format_html(
        '<svg class="icon" width="{}" height="{}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>',
        size,
        size,
        name,
    )


@register.filter
def local_name(value):
    if not value:
        return '—'
    return (value.name_sw or value.name) if get_language() == 'sw' else value.name


@register.filter
def localized(value, field):
    return (
        (getattr(value, field + '_sw', '') or getattr(value, field))
        if get_language() == 'sw'
        else getattr(value, field)
    )


@register.filter
def tone(status):
    return {
        'submitted': 'blue',
        'preliminary': 'blue',
        'awaiting': 'blue',
        'accepted': 'blue',
        'assigned': 'blue',
        'processing': 'blue',
        'information': 'amber',
        'review': 'purple',
        'head_review': 'amber',
        'approved': 'green',
        'completed': 'green',
        'closed': 'slate',
        'rejected': 'red',
        'pending': 'amber',
        'applied': 'green',
        'cancelled': 'slate',
        'urgent': 'red',
        'high': 'amber',
        'normal': 'blue',
        'low': 'slate',
    }.get(status, 'slate')


@register.filter
def audit_label(action):
    values = {
        'demo_signed_in': _('Demonstration sign-in'),
        'signed_in': _('Signed in'),
        'signed_out': _('Signed out'),
        'sign_in_failed': _('Sign-in failed'),
        'session_expired': _('Session expired'),
        'employee_created': _('Employee account created'),
        'employee_updated': _('Employee account updated'),
        'access_requested': _('Scoped access requested'),
        'access_decided': _('Access decision recorded'),
        'access_applied': _('Scoped access applied'),
        'access_revoked': _('Scoped access revoked'),
        'credentials_enrolled': _('Local credentials enrolled'),
        'unit_created': _('Organisation unit created'),
        'unit_updated': _('Organisation unit updated'),
        'profile_updated': _('Preferences updated'),
        'notifications_read': _('Notifications marked read'),
        'password_changed': _('Password changed'),
        'matter_viewed': _('Matter opened'),
        'legal_register_viewed': _('Legal register viewed'),
        'legal_dashboard_viewed': _('Legal dashboard viewed'),
        'legal_summary_requested': _('Legal summary requested'),
        'request_tracking_viewed': _('Request tracking opened'),
        'aggregate_viewed': _('Management summary viewed'),
        'records_index_viewed': _('Records index viewed'),
    }
    return values.get(action, action)
