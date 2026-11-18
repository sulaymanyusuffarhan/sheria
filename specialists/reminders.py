"""Deduplicated, in-app only deadline delivery with current-scope revalidation."""

from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext, gettext_noop

from casework.models import Case
from identity.models import Notification, RoleCode, User
from identity.policy import LEGAL_CAPABILITIES, Policy
from matters.models import Milestone
from matters.selectors import active_assignments, legal_scope

from .models import DeadlineNotice, Hearing

W = DeadlineNotice.Window
LABELS = {
    W.UPCOMING: gettext_noop('An authorised legal deadline is approaching'),
    W.TODAY: gettext_noop('An authorised legal deadline is due today'),
    W.OVERDUE: gettext_noop('A legal deadline requires supervisory follow-up'),
}
BODY = gettext_noop('Open the legal workspace to view authorised details.')
LATE = (
    Milestone.Kind.HEARING,
    Milestone.Kind.COMPLIANCE,
    Milestone.Kind.REASSESS,
    Milestone.Kind.CORRECTIVE,
)


def _active_milestones(qs):
    # Exclude unmanaged Phase 1 rows and resolved/replaced calendar entries.
    qs = qs.filter(is_cancelled=False, matter__workflow__isnull=False)
    return qs.filter(~Q(kind=Milestone.Kind.HEARING) | Q(hearing__status=Hearing.Status.SCHEDULED))


def _band(milestone, today):
    gap = (milestone.date - today).days
    if gap < 0 and milestone.kind in LATE:
        return W.OVERDUE
    if gap == 0:
        return W.TODAY
    if 0 < gap <= 7:
        return W.UPCOMING
    return None


def visibility(policy):
    """A delivered reminder disappears when superseded, resolved or out of current scope."""
    if not policy.any(LEGAL_CAPABILITIES):
        return DeadlineNotice.objects.none()
    today = timezone.localdate()
    milestones = _active_milestones(Milestone.objects.all()).filter(
        matter_id__in=legal_scope(policy).order_by().values('pk')
    )
    upcoming = milestones.none()
    if policy.has('matter.assigned'):
        upcoming = milestones.filter(
            matter_id__in=active_assignments(policy.user.is_demo)
            .filter(officer=policy.user)
            .order_by()
            .values('matter_id')
        )
    overdue = milestones.filter(date__lt=today, kind__in=LATE)
    if not policy.has('matter.institution'):
        overdue = overdue.filter(matter__department_id__in=policy.departments('matter.department'))
    return DeadlineNotice.objects.filter(recipient=policy.user, date=F('milestone__date')).filter(
        Q(window=W.OVERDUE, milestone__in=overdue)
        | Q(window=W.TODAY, milestone__in=upcoming.filter(date=today))
        | Q(
            window=W.UPCOMING,
            milestone__in=upcoming.filter(date__gt=today, date__lte=today + timedelta(days=7)),
        )
    )


@transaction.atomic
def _deliver(milestone_id, case_id, recipient_id, *, demo, now):
    recipient = (
        User.objects.select_for_update(no_key=True)
        .filter(pk=recipient_id, is_demo=demo, is_active=True)
        .first()
    )
    if not recipient:
        return False
    if not Case.objects.select_for_update(no_key=True).filter(pk=case_id).exists():
        return False
    policy = Policy(recipient)
    milestone = (
        _active_milestones(Milestone.objects.all())
        .filter(
            pk=milestone_id, matter_id=case_id, matter_id__in=legal_scope(policy).order_by().values('pk')
        )
        .select_related('matter')
        .first()
    )
    if not milestone or not (window := _band(milestone, timezone.localdate(now))):
        return False
    if window == W.OVERDUE:
        if not (
            policy.has('matter.institution')
            or milestone.matter.department_id in policy.departments('matter.department')
        ):
            return False
    elif (
        not policy.has('matter.assigned')
        or not active_assignments(demo).filter(matter_id=case_id, officer=recipient).exists()
    ):
        return False
    if DeadlineNotice.objects.filter(
        milestone=milestone, recipient=recipient, date=milestone.date, window=window
    ).exists():
        return False
    with translation.override('en'):
        title, body = gettext(LABELS[window]), gettext(BODY)
    with translation.override('sw'):
        title_sw, body_sw = gettext(LABELS[window]), gettext(BODY)
    notification = Notification.objects.create(
        recipient=recipient, title=title, body=body, title_sw=title_sw, body_sw=body_sw
    )
    DeadlineNotice.objects.create(
        milestone=milestone,
        recipient=recipient,
        date=milestone.date,
        window=window,
        notification=notification,
    )
    return True


def send_due(*, demo, now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    base = _active_milestones(Milestone.objects.filter(matter__is_demo=demo))
    near = base.filter(date__range=(today, today + timedelta(days=7)))
    late = base.filter(date__lt=today, kind__in=LATE)
    assignments = active_assignments(demo).filter(matter_id__in=near.order_by().values('matter_id'))
    sent = 0
    for assignment in assignments.iterator():
        for row in near.filter(matter_id=assignment.matter_id).iterator():
            sent += _deliver(row.pk, row.matter_id, assignment.officer_id, demo=demo, now=now)
    leads = User.objects.filter(
        is_active=True,
        is_demo=demo,
        role_grants__role_id__in=(RoleCode.SENIOR, RoleCode.HEAD),
        role_grants__revoked_at__isnull=True,
    ).distinct()
    for user in leads.iterator():
        policy = Policy(user)
        scope = legal_scope(policy)
        if not policy.has('matter.institution'):
            scope = scope.filter(department_id__in=policy.departments('matter.department'))
        for row in late.filter(matter_id__in=scope.order_by().values('pk')).iterator():
            sent += _deliver(row.pk, row.matter_id, user.pk, demo=demo, now=now)
    return sent


def reminder_link(notice):
    return reverse('matter_detail', args=[notice.milestone.matter_id])
