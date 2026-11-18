"""Registers, version history and an East Africa Time calendar, all scoped on read."""

from calendar import Calendar
from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import OuterRef, Q, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_safe

from casework.policy import cases_for, current_assignment
from casework.views import _apply, _form, _stale, _stale_response, record_access
from identity.policy import LEGAL_CAPABILITIES
from matters.models import Classification, Milestone, Status
from matters.selectors import legal_scope

from . import forms, services
from .models import Hearing, Proceeding
from .opinions import valid_issue
from .policy import MODULES, module_for_category, native_departments, strategy


def _module(slug):
    if slug not in MODULES:
        raise Http404
    return MODULES[slug]


def _scope(request):
    if not request.policy.any(LEGAL_CAPABILITIES):
        raise PermissionDenied
    return cases_for(request.policy)


def _case(request, module, pk):
    return get_object_or_404(
        _scope(request), pk=pk, matter__category_id__in=_module(module)['categories']
    )


def _context(module):
    return {'module': module, 'register': _module(module)}


@login_required
@require_safe
def index(request, module):
    config = _module(module)
    qs = _scope(request).filter(matter__category_id__in=config['categories'])
    related = services.DOSSIERS[module].__name__.lower()
    term = request.GET.get('q', '').strip()[:120]
    state = request.GET.get('status', '')
    if state in Status.values:
        qs = qs.filter(matter__status=state)
    if term:
        match = (
            Q(matter__title__icontains=term)
            | Q(matter__reference__icontains=term)
            | Q(**{f'{related}__reference__icontains': term})
        )
        if module == 'litigation':
            match |= Q(litigation__external_number__icontains=term) | Q(
                litigation__court__icontains=term
            )
        qs = qs.filter(match)
    if module == 'litigation':
        next_hearing = Hearing.objects.filter(
            litigation_id=OuterRef('pk'), status=Hearing.Status.SCHEDULED, starts_at__gte=timezone.now()
        ).values('starts_at')[:1]
        qs = qs.annotate(next_hearing=Subquery(next_hearing))
    qs = qs.select_related(related).order_by('-matter__opened_at', '-pk')
    page = Paginator(qs, 15).get_page(request.GET.get('page'))
    for row in page:
        row.specialist = getattr(row, related, None)
    record_access(request, f'{module}_register_viewed', '')
    return render(
        request,
        'specialists/index.html',
        _context(module)
        | {
            'page_obj': page,
            'q': term,
            'status': state,
            'statuses': Status.choices,
            'may_create': native_departments(request.policy).exists(),
        },
    )


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def create(request, module):
    config = _module(module)
    _scope(request)
    if not native_departments(request.policy).exists():
        raise PermissionDenied
    posted = request.method == 'POST'
    form = forms.InternalForm(request.POST if posted else None, module=module, policy=request.policy)
    status = 400 if posted else 200
    if posted and form.is_valid():
        status, case = _apply(form, lambda: services.open_internal(request, module, form.cleaned_data))
        if case:
            if case.matter.classification != Classification.INTERNAL:
                messages.success(
                    request,
                    _(
                        'Protected matter %(reference)s registered. Named clearance is required before anyone can open it.'
                    )
                    % {'reference': case.matter.reference},
                )
                return redirect('specialist_index', module)
            messages.success(request, _('Internal matter registered. It has no staff-facing record.'))
            return redirect('specialist_detail', module, case.pk)
    return _form(
        request,
        form,
        title=_('Open internal matter'),
        description=_(
            'Create a matter for %(register)s. Use the staff portal for requests that need a tracked staff response.'
        )
        % {'register': config['title']},
        back=reverse('specialist_index', args=[module]),
        button=_('Register matter'),
        status=status,
    )


def _details(request, case, module, record):
    policy = request.policy
    if module == 'litigation':
        proceedings = record.proceedings.select_related('recorded_by', 'evidence')
        if not strategy(policy, case):
            proceedings = proceedings.exclude(kind=Proceeding.Kind.STRATEGY)
        return {
            'hearings': record.hearings.select_related('replaces'),
            'parties': record.parties.all(),
            'next_hearing': record.hearings.filter(
                status=Hearing.Status.SCHEDULED, starts_at__gte=timezone.now()
            ).first(),
            'proceedings': proceedings,
            'costs': record.costs.select_related('recorded_by'),
            'outcome': getattr(record, 'outcome', None),
            'closure': getattr(record, 'closure', None),
        }
    if module == 'opinions':
        versions = record.versions.select_related(
            'authored_by', 'senior_review__reviewer', 'issue__review', 'evidence'
        )
        selected_id = request.GET.get('version')
        if selected_id:
            if not selected_id.isascii() or not selected_id.isdecimal() or len(selected_id) > 8:
                raise Http404
            selected = get_object_or_404(versions, number=int(selected_id))
        else:
            selected = versions.first()
        issue = getattr(selected, 'issue', None)
        if issue and not valid_issue(case, issue):
            raise Http404
        return {
            'versions': versions,
            'selected': selected,
            'senior': getattr(selected, 'senior_review', None),
            'issue': issue,
            'latest_review': case.reviews.select_related('decision').first(),
        }
    if module == 'rights':
        licences = list(
            record.licences.select_related('prior', 'provenance', 'execution__version__contract')
        )
        contract_scope = services.corporate_contracts(policy, case).filter(
            pk__in={row.execution_id for row in licences if row.execution_id}
        )
        visible = set(contract_scope.values_list('pk', flat=True))
        today = timezone.localdate()
        for row in licences:
            row.visible_execution = row.execution if row.execution_id in visible else None
            row.window = (
                'expired' if row.ends_on < today else 'future' if row.starts_on > today else 'current'
            )
        incidents = list(
            record.incidents.select_related(
                'licence', 'resolution', 'evidence', 'referred_matter__matter'
            )
        )
        refs = services.litigation_referrals(policy, case).filter(
            pk__in={row.referred_matter_id for row in incidents if row.referred_matter_id}
        )
        visible_refs = set(refs.values_list('pk', flat=True))
        for row in incidents:
            row.visible_referral = (
                row.referred_matter if row.referred_matter_id in visible_refs else None
            )
        return {'licences': licences, 'incidents': incidents}
    source = services.latest_source(record)
    return {
        'source': source,
        'sources': record.sources.select_related('author', 'verification__verified_by'),
        'current_assessment': record.assessments.filter(source=source).first() if source else None,
        'assessments': record.assessments.select_related('source', 'evidence', 'assessed_by'),
        'corrective_actions': record.assessments.filter(corrective_action__isnull=False)
        .select_related('corrective_action__responsible')
        .prefetch_related(
            'corrective_action__remediations__decision__verified_by',
            'corrective_action__remediations__evidence',
            'corrective_action__remediations__submitted_by',
        ),
    }


@login_required
@require_safe
def detail(request, module, pk):
    case = _case(request, module, pk)
    record = services.dossier(case, module)
    current = current_assignment(case)
    actions = [
        (command, services.COMMANDS[command])
        for command in ('register', *services.TYPED_COMMANDS[module])
        if services.action_allowed(request.policy, case, module, command, record)
    ]
    record_access(request, f'{module}_opened', pk)
    return render(
        request,
        'specialists/detail.html',
        _context(module)
        | {
            'case': case,
            'record': record,
            'actions': actions,
            'assignment': current,
            **(_details(request, case, module, record) if record else {}),
        },
    )


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def action(request, module, pk, command):
    case = _case(request, module, pk)
    if command not in ('register', *services.TYPED_COMMANDS[module]):
        raise Http404
    record = services.dossier(case, module)
    posted = request.method == 'POST'
    if posted and _stale(request, case):
        return _stale_response(request, case)
    if not services.action_allowed(request.policy, case, module, command, record):
        raise PermissionDenied
    form = forms.CommandForm(
        request.POST if posted else None,
        case=case,
        module=module,
        command=command,
        record=record,
        policy=request.policy,
    )
    status = 400 if posted else 200
    if posted and form.is_valid():
        status, result = _apply(
            form, lambda: services.run(request, pk, module, command, form.cleaned_data)
        )
        if result:
            messages.success(request, _('Specialist action recorded.'))
            return redirect('specialist_detail', module, pk)
    return _form(
        request,
        form,
        title=services.COMMANDS[command],
        description=_(
            'Changes are recorded against the current matter revision. Existing history is preserved.'
        ),
        back=reverse('specialist_detail', args=[module, pk]),
        button=services.COMMANDS[command],
        context={
            'case': case,
            'draft': services.latest_draft(case) if command == 'opinion_senior' else None,
            'source_version': services.latest_source(record)
            if command in ('cmp_verify', 'cmp_assess')
            else None,
        },
        status=status,
    )


def _month(raw, today):
    if not raw:
        return today.replace(day=1)
    try:
        value = date.fromisoformat(f'{raw}-01')
    except (ValueError, OverflowError) as exc:
        raise Http404 from exc
    if not 1900 <= value.year <= 2100 or len(raw) != 7:
        raise Http404
    return value


@login_required
@require_safe
def calendar(request):
    scope = _scope(request).order_by().values('pk')
    today = timezone.localdate()
    month = _month(request.GET.get('month'), today)
    previous = (month - timedelta(days=1)).replace(day=1)
    following = (month + timedelta(days=32)).replace(day=1)
    zone = timezone.get_current_timezone()
    start, end = (datetime.combine(day, time.min, tzinfo=zone) for day in (month, following))
    show_history = request.GET.get('history') == '1'
    rows = Hearing.objects.filter(litigation_id__in=scope, starts_at__gte=start, starts_at__lt=end)
    if not show_history:
        rows = rows.filter(status=Hearing.Status.SCHEDULED)
    summary = {}
    for moment in rows.values_list('starts_at', flat=True):
        day = timezone.localdate(moment)
        summary[day] = summary.get(day, 0) + 1
    weeks = [
        [
            {
                'day': day,
                'count': summary.get(day, 0),
                'link': day.month == month.month and bool(summary.get(day)),
            }
            for day in week
        ]
        for week in Calendar().monthdatescalendar(month.year, month.month)
    ]
    day_filter = request.GET.get('day', '')
    selected = None
    if day_filter:
        try:
            selected = date.fromisoformat(day_filter)
        except ValueError as exc:
            raise Http404 from exc
        if not month <= selected < following:
            raise Http404
        rows = rows.filter(starts_at__date=selected)
    page = Paginator(rows.select_related('litigation__case__matter'), 16).get_page(
        request.GET.get('page')
    )
    record_access(request, 'court_calendar_viewed', '')
    return render(
        request,
        'specialists/calendar.html',
        {
            'month': month,
            'weeks': weeks,
            'today': today,
            'history': show_history,
            'previous': previous if previous.year >= 1900 else None,
            'following': following if following.year <= 2100 else None,
            'page_obj': page,
            'selected': selected,
        },
    )


@login_required
@require_safe
def deadlines(request):
    _scope(request)
    scope = legal_scope(request.policy).order_by().values('pk')
    today = timezone.localdate()
    overdue = request.GET.get('window') == 'overdue'
    rows = Milestone.objects.filter(is_cancelled=False, matter_id__in=scope).exclude(
        kind=Milestone.Kind.HEARING
    )
    if overdue:
        rows = (
            rows.filter(date__lt=today)
            .exclude(kind__in=(Milestone.Kind.RIGHTS, Milestone.Kind.EXPIRY))
            .order_by('-date', '-pk')
        )
    else:
        rows = rows.filter(date__range=(today, today + timedelta(days=30)))
    page = Paginator(rows.select_related('matter__category'), 18).get_page(request.GET.get('page'))
    for row in page:
        row.module = module_for_category(row.matter.category_id)
    record_access(request, 'legal_deadlines_viewed', '')
    return render(
        request, 'specialists/deadlines.html', {'page_obj': page, 'today': today, 'overdue': overdue}
    )
