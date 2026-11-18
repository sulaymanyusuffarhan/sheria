"""Staff and legal workspaces. Introduced: 2026-11-18; revisions: Git."""

from datetime import timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_safe
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.response import Response

from casework.views import legal_panel, staff_panel
from identity.models import AuditLog, Department, Directorate, User
from identity.policy import LEGAL_CAPABILITIES, STAFF_CAPABILITIES
from identity.security import audit
from matters.models import STAFF_TERMINAL, Category, StaffStatus, Status
from matters.selectors import (
    aggregate_scope,
    category_chart,
    chart_months,
    display_assignments,
    legal_metrics,
    legal_scope,
    records_scope,
    staff_metrics,
    staff_scope,
    with_assignment,
)


class LegalFilter(forms.Form):
    period = forms.ChoiceField(
        label=gettext_lazy('Reporting period'),
        required=False,
        choices=[
            ('all', gettext_lazy('All time')),
            ('180', gettext_lazy('Last 180 days')),
            ('90', gettext_lazy('Last 90 days')),
            ('30', gettext_lazy('Last 30 days')),
        ],
    )
    directorate = forms.ChoiceField(label=gettext_lazy('Directorate'), required=False)
    department = forms.ChoiceField(label=gettext_lazy('Department'), required=False)
    category = forms.ChoiceField(label=gettext_lazy('Category'), required=False)
    officer = forms.ChoiceField(label=gettext_lazy('Legal officer'), required=False)

    def __init__(self, *args, base, language, **kwargs):
        super().__init__(*args, **kwargs)
        dept_names = 'name_sw' if language == 'sw' else 'name'
        depts = Department.objects.filter(pk__in=base.order_by().values('department_id'))
        dirs = Directorate.objects.filter(pk__in=depts.order_by().values('directorate_id'))
        categories = Category.objects.filter(pk__in=base.order_by().values('category_id')).order_by(
            dept_names
        )
        officers = User.objects.filter(pk__in=base.order_by().values('current_officer')).order_by(
            'first_name', 'last_name'
        )

        def scoped_choices(units, title):
            return [('', title)] + [
                (str(unit.pk), (unit.name_sw or unit.name) if language == 'sw' else unit.name)
                for unit in units.order_by(dept_names, 'name')
            ]

        self.fields['directorate'].choices = scoped_choices(dirs, _('All directorates'))
        self.fields['department'].choices = scoped_choices(depts, _('All departments'))
        self.fields['category'].choices = [('', _('All categories'))] + [
            (item.pk, item.name_sw if language == 'sw' else item.name) for item in categories
        ]
        self.fields['officer'].choices = [('', _('All officers'))] + [
            (str(user.pk), user.display_name) for user in officers
        ]


class RegisterFilter(LegalFilter):
    q = forms.CharField(
        required=False,
        max_length=120,
        label=gettext_lazy('Search'),
        widget=forms.TextInput(attrs={'placeholder': gettext_lazy('Reference or matter title')}),
    )
    status = forms.ChoiceField(
        label=gettext_lazy('Status'),
        required=False,
        choices=[('', gettext_lazy('All statuses')), *Status.choices],
    )
    start = forms.DateField(
        label=gettext_lazy('From date'),
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
    )
    end = forms.DateField(
        label=gettext_lazy('To date'),
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
    )

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            self.add_error('end', _('The end date must be on or after the start date.'))
        return data


def _legal(request):
    if not request.policy.any(LEGAL_CAPABILITIES):
        raise PermissionDenied
    return with_assignment(legal_scope(request.policy), request.user.is_demo)


def _apply_filters(qs, form, today):
    if not form.is_valid():
        return qs.none(), None, None
    data = form.cleaned_data
    start, end = data.get('start'), data.get('end')
    period = data.get('period')
    if period and period != 'all':
        cutoff = today - timedelta(days=int(period) - 1)
        start = max(start, cutoff) if start else cutoff
        end = min(end, today) if end else today
    if start:
        qs = qs.filter(opened_at__date__gte=start)
    if end:
        qs = qs.filter(opened_at__date__lte=end)
    for key, field in [
        ('directorate', 'department__directorate_id'),
        ('department', 'department_id'),
        ('category', 'category_id'),
        ('officer', 'current_officer'),
        ('status', 'status'),
    ]:
        if data.get(key):
            qs = qs.filter(**{field: data[key]})
    if q := data.get('q'):
        qs = qs.filter(Q(reference__icontains=q) | Q(title__icontains=q))
    return qs, start, end


@login_required
@require_safe
def admin_home(request):
    if request.policy.any(LEGAL_CAPABILITIES):
        return legal_dashboard(request)
    if request.policy.landing in ('staff_dashboard', 'profile'):
        raise PermissionDenied
    return redirect(request.policy.landing)


@login_required
@require_safe
def legal_dashboard(request):
    today = timezone.localdate()
    base = _legal(request)
    form = LegalFilter(request.GET or {'period': 'all'}, base=base, language=request.LANGUAGE_CODE)
    qs, first, last = _apply_filters(base, form, today)
    metrics, extra, milestones = legal_metrics(qs, today)
    trend = chart_months(
        qs, today, first=first if not last or not first or first <= last else None, last=last
    )
    chart = category_chart(qs, request.LANGUAGE_CODE)
    audit(request, 'legal_dashboard_viewed', area=AuditLog.Area.LEGAL)
    return render(
        request,
        'legal_dashboard.html',
        {
            'filter_form': form,
            'today': today,
            'metrics': metrics,
            'extra_metrics': extra,
            'trend': trend,
            'chart': chart,
            'recent': display_assignments(qs, request.user.is_demo)[:6],
            'milestones': milestones.select_related('matter')[:4],
            'dashboard': 'legal',
        },
    )


@login_required
@require_safe
def matter_register(request):
    base = _legal(request)
    form = RegisterFilter(request.GET or {}, base=base, language=request.LANGUAGE_CODE)
    qs, _, _ = _apply_filters(base, form, timezone.localdate())
    page = Paginator(display_assignments(qs, request.user.is_demo), 14).get_page(request.GET.get('page'))
    audit(request, 'legal_register_viewed', area=AuditLog.Area.LEGAL)
    return render(request, 'matter_register.html', {'filter_form': form, 'page_obj': page})


@login_required
@require_safe
def matter_detail(request, pk):
    matter = get_object_or_404(display_assignments(_legal(request), request.user.is_demo), pk=pk)
    audit(request, 'matter_viewed', area=AuditLog.Area.LEGAL, target=matter.pk)
    return render(
        request,
        'matter_detail.html',
        {
            'matter': matter,
            'milestones': matter.milestones.filter(is_cancelled=False),
            **legal_panel(request, matter),
        },
    )


def _staff(request):
    if not request.policy.any(STAFF_CAPABILITIES):
        raise PermissionDenied
    department = request.GET.get('scope') == 'department'
    if department and not request.policy.has('request.department'):
        raise PermissionDenied
    return staff_scope(request.policy, department), department


@login_required
@require_safe
def staff_dashboard(request):
    qs, department = _staff(request)
    deadlines = (
        qs.filter(due_on__gte=timezone.localdate())
        .exclude(public_status__in=STAFF_TERMINAL)
        .order_by('due_on')[:5]
    )
    return render(
        request,
        'staff_dashboard.html',
        {
            'metrics': staff_metrics(qs),
            'recent': qs[:5],
            'deadlines': deadlines,
            'department_scope': department,
            'dashboard': 'staff',
        },
    )


def _filter_staff(request, qs):
    q = request.GET.get('q', '').strip()[:120]
    status = request.GET.get('status', 'all') or 'all'
    upcoming = request.GET.get('due') == 'upcoming'
    if q:
        qs = qs.filter(Q(matter__reference__icontains=q) | Q(title__icontains=q))
    if status in StaffStatus.values:
        qs = qs.filter(public_status=status)
    elif status == 'finished':
        qs = qs.filter(public_status__in=(StaffStatus.COMPLETED, StaffStatus.CLOSED))
    elif status != 'all':
        qs = qs.none()
    if upcoming:
        qs = (
            qs.filter(due_on__gte=timezone.localdate())
            .exclude(public_status__in=STAFF_TERMINAL)
            .order_by('due_on', '-submitted_at', '-pk')
        )
    return qs, q, status, upcoming


@login_required
@require_safe
def staff_requests(request):
    qs, department = _staff(request)
    qs, q, status, upcoming = _filter_staff(request, qs)
    return render(
        request,
        'staff_requests.html',
        {
            'page_obj': Paginator(qs, 14).get_page(request.GET.get('page')),
            'q': q,
            'status': status,
            'department_scope': department,
            'upcoming': upcoming,
            'status_options': [
                ('all', _('All statuses')),
                ('finished', _('Completed or closed')),
                *StaffStatus.choices,
            ],
        },
    )


@login_required
@require_safe
def staff_request(request, pk):
    qs, department = _staff(request)
    record = get_object_or_404(qs, pk=pk)
    audit(request, 'request_tracking_viewed', area=AuditLog.Area.LEGAL, target=record.pk)
    return render(
        request,
        'staff_detail.html',
        {
            'record': record,
            'department_scope': department,
            'updates': record.updates.filter(published_at__lte=timezone.now()),
            **staff_panel(request, record, department),
        },
    )


@login_required
@require_safe
def management_dashboard(request):
    if not request.policy.has('reports.aggregate'):
        raise PermissionDenied
    qs = aggregate_scope(request.policy)
    # One stable unfiltered projection only. No small-group drill-through or offsetting filters.
    groups = list(qs.order_by().values('status').annotate(total=Count('pk')))
    summary = {row['status']: row['total'] for row in groups}
    total = sum(summary.values())
    completed = summary.get(Status.COMPLETED, 0)
    other = total - completed
    metrics = [
        {
            'label': _('Reportable matters'),
            'value': total if not 0 < completed < 3 and not 0 < other < 3 else '—',
            'icon': 'briefcase',
            'tone': 'blue',
        },
        {
            'label': _('Completed'),
            'value': completed if not 0 < completed < 3 else '—',
            'icon': 'check-circle',
            'tone': 'green',
        },
        {
            'label': _('Other outcomes and open matters'),
            'value': other if not 0 < other < 3 else '—',
            'icon': 'clock',
            'tone': 'amber',
        },
    ]
    audit(request, 'aggregate_viewed', area=AuditLog.Area.LEGAL)
    return render(request, 'management.html', {'metrics': metrics})


@login_required
@require_safe
def records_register(request):
    if not request.policy.has('records.department'):
        raise PermissionDenied
    qs = records_scope(request.policy)
    q = request.GET.get('q', '').strip()[:100]
    if q:
        qs = qs.filter(reference__icontains=q)
    audit(request, 'records_index_viewed', area=AuditLog.Area.LEGAL)
    return render(
        request,
        'records.html',
        {'page_obj': Paginator(qs, 14).get_page(request.GET.get('page')), 'q': q},
    )


@api_view(['GET'])
def api_staff_requests(request):
    qs, _department = _staff(request)
    qs, _query, status, _upcoming = _filter_staff(request, qs)
    if status not in ('all', 'finished', *StaffStatus.values):
        raise APIValidationError({'status': _('Choose a valid status.')})
    page = Paginator(qs, 20).get_page(request.GET.get('page'))
    return Response(
        {
            'count': page.paginator.count,
            'page': page.number,
            'results': [
                {
                    'id': record.pk,
                    'reference': record.matter.reference,
                    'title': record.title,
                    'status': record.public_status,
                    'status_label': record.get_public_status_display(),
                    'submitted_at': record.submitted_at,
                    'due_on': record.due_on,
                }
                for record in page
            ],
        }
    )


@api_view(['GET'])
def api_legal_summary(request):
    today = timezone.localdate()
    base = _legal(request)
    form = LegalFilter(request.GET, base=base, language=request.LANGUAGE_CODE)
    qs, _, _ = _apply_filters(base, form, today)
    if form.errors:
        raise APIValidationError({field: list(errors) for field, errors in form.errors.items()})
    main, extra, _ = legal_metrics(qs, today)
    audit(request, 'legal_summary_requested', area=AuditLog.Area.LEGAL)
    return Response(
        {
            'metrics': [
                {'key': item['key'], 'label': item['label'], 'count': item['value']}
                for item in [*main, *extra]
            ]
        }
    )


def csrf_failure(request, reason=''):
    return render(
        request,
        'error.html',
        {
            'code': '403',
            'title': _('Form could not be verified'),
            'detail': _('Reload the page and submit the form again.'),
        },
        status=403,
    )


def forbidden(request, exception):
    return render(
        request,
        'error.html',
        {
            'code': '403',
            'title': _('Access restricted'),
            'detail': _('Your current role does not allow this action.'),
        },
        status=403,
    )


def not_found(request, exception):
    return render(
        request,
        'error.html',
        {
            'code': '404',
            'title': _('Page not found'),
            'detail': _('This page is not available in your workspace.'),
        },
        status=404,
    )


def server_error(request):
    return render(
        request,
        'error.html',
        {
            'code': '500',
            'title': _('Something went wrong'),
            'detail': _('Try again later or contact your ICT support desk.'),
        },
        status=500,
    )
