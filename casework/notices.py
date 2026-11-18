"""Transactionally delivered in-app notices; no legal metadata in notification text. Revisions: Git."""

from django.db.models import Q
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext, gettext_noop

from identity.models import Notification, RoleCode, User
from identity.policy import Policy
from matters.selectors import legal_scope, staff_scope

from .models import EventNotice
from .policy import current_assignment, own_case, supervises

STAFF_NOTICES = {
    'submit': gettext_noop('Your legal request was submitted'),
    'accept': gettext_noop('Your legal request was accepted'),
    'ask': gettext_noop('Legal Services requested more information'),
    'verify': gettext_noop('Your response was verified'),
    'approve': gettext_noop('Head review is complete. The response is pending issue.'),
    'reject': gettext_noop('A decision is available for your request'),
    'issue': gettext_noop('Your approved legal response is available'),
    'close': gettext_noop('Your legal request was closed'),
    'reopen': gettext_noop('A new review is in progress for your request'),
}
DLS_NOTICES = {
    'submit': gettext_noop('New request for preliminary review'),
    'accept': gettext_noop('Accepted request to assign'),
    'assign': gettext_noop('Legal work assigned to you'),
    'reply': gettext_noop('A staff response is ready for verification'),
    'submit_review': gettext_noop('A frozen review is awaiting a Head decision'),
    'approve': gettext_noop('Head decision recorded'),
    'return': gettext_noop('A review was returned for changes'),
    'conditional': gettext_noop('Conditions must be verified before clearance'),
    'reject': gettext_noop('A review decision was recorded'),
    'reopen': gettext_noop('A contract needs a new version review'),
}
STAFF_BODY = gettext_noop('Open your staff portal to see the published status and next steps.')
DLS_BODY = gettext_noop('Open the legal workspace to view authorised details.')


def _emit(event, recipient, code, *, staff=False):
    if EventNotice.objects.filter(event=event, recipient=recipient).exists():
        return
    label = STAFF_NOTICES[code] if staff else DLS_NOTICES[code]
    body = STAFF_BODY if staff else DLS_BODY
    with translation.override('en'):
        title, content = gettext(label), gettext(body)
    with translation.override('sw'):
        title_sw, content_sw = gettext(label), gettext(body)
    record = Notification.objects.create(
        recipient=recipient, title=title, body=content, title_sw=title_sw, body_sw=content_sw
    )
    EventNotice.objects.create(
        event=event, recipient=recipient, notification=record, staff_audience=staff
    )


def notify(event, kind):
    case = event.case
    owner = case.submitted_by
    if kind in STAFF_NOTICES and own_case(Policy(owner), case):
        _emit(event, owner, kind, staff=True)
    if kind not in DLS_NOTICES:
        return
    if kind in ('submit', 'accept', 'reply', 'submit_review'):
        candidates = User.objects.filter(
            is_active=True,
            is_demo=case.matter.is_demo,
            role_grants__role_id__in=(RoleCode.HEAD,)
            if kind == 'submit_review'
            else (RoleCode.HEAD, RoleCode.SENIOR),
            role_grants__revoked_at__isnull=True,
        ).distinct()
    else:
        candidates = []
    recipients = {}
    for user in candidates:
        policy = Policy(user)
        if (
            user.pk != owner.pk
            and (kind != 'submit_review' or user.pk != event.actor_id)
            and supervises(policy, case)
            and legal_scope(policy).filter(pk=case.pk).exists()
        ):
            recipients[user.pk] = user
    if kind in ('assign', 'reply', 'approve', 'return', 'conditional', 'reject', 'reopen') and (
        assigned := current_assignment(case)
    ):
        officer = assigned.officer
        if legal_scope(Policy(officer)).filter(pk=case.pk).exists():
            recipients[officer.pk] = officer
    for user in recipients.values():
        _emit(event, user, kind)


def visible_inbox(policy):
    """Suppress workflow notices and links when the recipient loses current record scope."""
    visible_legal = legal_scope(policy).order_by().values('pk')
    visible_own = staff_scope(policy).order_by().values('pk')
    return policy.user.notifications.filter(
        Q(workflow_notice__isnull=True)
        | Q(workflow_notice__staff_audience=True, workflow_notice__event__case_id__in=visible_own)
        | Q(workflow_notice__staff_audience=False, workflow_notice__event__case_id__in=visible_legal)
    )


def link_notices(page):
    for notice in page:
        link = getattr(notice, 'workflow_notice', None)
        if link:
            notice.case_url = reverse(
                'staff_request' if link.staff_audience else 'matter_detail', args=[link.event.case_id]
            )
    return page
