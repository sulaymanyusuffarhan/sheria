"""Authorised queries and aggregate calculations. Introduced: 2026-11-18; revisions: Git."""

from datetime import timedelta
from math import ceil

from django.db.models import Count, Exists, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import TruncMonth
from django.utils import formats, timezone
from django.utils.translation import gettext as _

from .models import (
    TERMINAL,
    Classification,
    LegalAssignment,
    LegalMatter,
    LegalRequest,
    MatterAccess,
    Milestone,
    StaffStatus,
    Status,
)


def staff_scope(policy, department=False):
    qs = LegalRequest.objects.select_related('matter__category', 'matter__department').only(
        'matter_id',
        'title',
        'summary',
        'submitted_at',
        'due_on',
        'public_status',
        'matter__reference',
        'matter__category__name',
        'matter__category__name_sw',
        'matter__department__name',
        'matter__department__name_sw',
    )
    if not policy.user.is_authenticated:
        return qs.none()
    qs = qs.filter(
        matter__is_demo=policy.user.is_demo,
        matter__department__is_demo=policy.user.is_demo,
        requester__is_demo=policy.user.is_demo,
    )
    if department:
        return qs.filter(
            matter__department_id__in=policy.departments('request.department'),
            department_visible=True,
            matter__classification=Classification.INTERNAL,
        )
    if policy.has('request.own'):
        return qs.filter(requester=policy.user)
    return qs.none()


def active_assignments(demo):
    now = timezone.now()
    return (
        LegalAssignment.objects.filter(
            starts_at__lte=now, revoked_at__isnull=True, officer__is_active=True, officer__is_demo=demo
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .order_by('-starts_at', '-pk')
    )


def legal_scope(policy):
    qs = LegalMatter.objects.select_related('category', 'department__directorate')
    if not policy.user.is_authenticated:
        return qs.none()
    qs = qs.filter(is_demo=policy.user.is_demo, department__is_demo=policy.user.is_demo)
    if not policy.has('matter.institution'):
        allowed = Q(department_id__in=policy.departments('matter.department'))
        if policy.has('matter.assigned'):
            allowed |= Q(
                Exists(
                    active_assignments(policy.user.is_demo).filter(
                        matter_id=OuterRef('pk'), officer=policy.user
                    )
                )
            )
        qs = qs.filter(allowed)
    named_clearance = MatterAccess.objects.filter(
        user=policy.user,
        matter_id=OuterRef('pk'),
        classification=OuterRef('classification'),
        expires_at__gt=timezone.now(),
        revoked_at__isnull=True,
    )
    return qs.filter(Q(classification=Classification.INTERNAL) | Q(Exists(named_clearance)))


def with_assignment(qs, demo):
    current = active_assignments(demo).filter(matter_id=OuterRef('pk'))
    return qs.annotate(
        current_officer=Subquery(current.values('officer_id')[:1]),
        assignment_due=Subquery(current.values('due_on')[:1]),
    )


def display_assignments(qs, demo):
    return qs.prefetch_related(
        Prefetch(
            'assignments',
            queryset=active_assignments(demo).select_related('officer'),
            to_attr='current_assignments',
        )
    )


def aggregate_scope(policy):
    qs = LegalMatter.objects.filter(classification=Classification.INTERNAL)
    if not policy.has('reports.aggregate'):
        return qs.none()
    return qs.filter(is_demo=policy.user.is_demo, department__is_demo=policy.user.is_demo)


def records_scope(policy):
    return (
        LegalMatter.objects.filter(
            classification=Classification.INTERNAL,
            is_demo=policy.user.is_demo,
            department__id__in=policy.departments('records.department'),
            department__is_demo=policy.user.is_demo,
        )
        .select_related('department', 'category')
        .only(
            'reference',
            'opened_at',
            'classification',
            'department__name',
            'department__name_sw',
            'category__name',
            'category__name_sw',
        )
    )


def status_totals(qs, field='status'):
    return {
        item[field]: item['total'] for item in qs.order_by().values(field).annotate(total=Count('pk'))
    }


def staff_metrics(qs):
    totals = status_totals(qs, 'public_status')
    definitions = [
        ('all', _('Total requests'), 'files', 'blue', sum(totals.values())),
        (StaffStatus.AWAITING, StaffStatus.AWAITING.label, 'inbox', 'amber', None),
        (StaffStatus.PROCESSING, StaffStatus.PROCESSING.label, 'clock', 'blue', None),
        (StaffStatus.INFORMATION, StaffStatus.INFORMATION.label, 'message', 'purple', None),
        (
            'finished',
            _('Completed or closed'),
            'check-circle',
            'green',
            totals.get(StaffStatus.COMPLETED, 0) + totals.get(StaffStatus.CLOSED, 0),
        ),
        (StaffStatus.REJECTED, StaffStatus.REJECTED.label, 'x-circle', 'red', None),
    ]
    return [
        {
            'key': key,
            'label': label,
            'icon': icon,
            'tone': tone,
            'value': totals.get(key, 0) if total is None else total,
        }
        for key, label, icon, tone, total in definitions
    ]


def legal_metrics(qs, today):
    totals = status_totals(qs)

    def total(*states):
        return sum(totals.get(state, 0) for state in states)

    active = qs.exclude(status__in=TERMINAL)
    upcoming = Milestone.objects.filter(
        matter_id__in=qs.order_by().values('pk'),
        is_cancelled=False,
        date__range=(today, today + timedelta(days=30)),
    )
    main = [
        dict(
            key='total',
            label=_('Total legal matters'),
            value=sum(totals.values()),
            icon='briefcase',
            tone='blue',
            note=_('In the selected period'),
        ),
        dict(
            key='review',
            label=_('Under legal review'),
            value=total(Status.REVIEW),
            icon='search-file',
            tone='purple',
            note=_('Being assessed by Legal Services'),
        ),
        dict(
            key='head_review',
            label=_('Pending approvals'),
            value=total(Status.HEAD_REVIEW),
            icon='check-shield',
            tone='amber',
            note=_('Awaiting Head of Legal Services'),
        ),
        dict(
            key='completed',
            label=_('Completed matters'),
            value=total(Status.COMPLETED),
            icon='check-circle',
            tone='green',
            note=_('Marked completed in the register'),
        ),
    ]
    other = [
        ('new', _('New requests'), total(Status.SUBMITTED, Status.PRELIMINARY), 'inbox'),
        (
            'unassigned',
            _('Unassigned accepted matters'),
            active.filter(status=Status.ACCEPTED, current_officer__isnull=True).count(),
            'user',
        ),
        ('overdue', _('Overdue assignments'), active.filter(assignment_due__lt=today).count(), 'alarm'),
        ('litigation', _('Active litigation'), active.filter(category_id='litigation').count(), 'gavel'),
        (
            'contracts_review',
            _('Contracts awaiting review'),
            active.filter(
                category_id='contract',
                status__in=(
                    Status.SUBMITTED,
                    Status.PRELIMINARY,
                    Status.ACCEPTED,
                    Status.ASSIGNED,
                    Status.REVIEW,
                ),
            ).count(),
            'file',
        ),
        (
            'high_risk_contracts',
            _('High-risk contracts'),
            active.filter(category_id='contract', risk=LegalMatter.Risk.HIGH).count(),
            'flag',
        ),
        (
            'hearings',
            _('Hearings in 30 days'),
            upcoming.filter(kind=Milestone.Kind.HEARING).count(),
            'calendar',
        ),
        (
            'expiries',
            _('Expiries in 30 days'),
            upcoming.filter(kind=Milestone.Kind.EXPIRY).count(),
            'clock',
        ),
    ]
    extras = [
        {'key': key, 'label': label, 'value': value, 'icon': icon} for key, label, value, icon in other
    ]
    return main, extras, upcoming


def chart_months(qs, today, *, first=None, last=None):
    last = last or today
    month = last.replace(day=1)
    months = [month]
    for _index in range(5):
        month = (month - timedelta(days=1)).replace(day=1)
        months.insert(0, month)
    months = [month for month in months if not first or month >= first.replace(day=1)]
    counts = {
        item['month'].date(): item['total']
        for item in qs.filter(opened_at__date__gte=months[0], opened_at__date__lte=last)
        .order_by()
        .annotate(month=TruncMonth('opened_at'))
        .values('month')
        .annotate(total=Count('pk'))
    }
    values = [(formats.date_format(month, 'M'), counts.get(month, 0)) for month in months]
    step = max(1, ceil(max(value for _, value in values) / 4))
    scale = step * 4
    points = [
        {
            'label': label,
            'value': value,
            'x': 44 + (570 * i // max(len(values) - 1, 1)) if len(values) > 1 else 329,
            'y': round(165 - 145 * value / scale, 2),
        }
        for i, (label, value) in enumerate(values)
    ]
    coords = ' '.join(f'{point["x"]},{point["y"]}' for point in points)
    area = f'{points[0]["x"]},165 {coords} {points[-1]["x"]},165'
    return {
        'points': points,
        'coords': coords,
        'area': area,
        'ticks': [(index * step, round(165 - index * 145 / 4, 2)) for index in range(5)],
    }


def category_chart(qs, lang):
    field = 'category__name_sw' if lang == 'sw' else 'category__name'
    categories = list(qs.order_by().values(field).annotate(total=Count('pk')).order_by('-total', field))
    total = sum(item['total'] for item in categories)
    groups = [(row[field], row['total']) for row in categories[:4]]
    if rest := sum(row['total'] for row in categories[4:]):
        groups.append((_('Other categories'), rest))
    offset = 0
    slices = []
    for index, (label, count) in enumerate(groups):
        portion = count * 100 / total
        slices.append(
            {
                'label': label,
                'count': count,
                'size': round(portion, 4),
                'gap': round(100 - portion, 4),
                'offset': round(-offset, 4),
                'tone': index,
            }
        )
        offset += portion
    return {'total': total, 'slices': slices}
