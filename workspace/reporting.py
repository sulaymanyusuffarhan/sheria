"""Fixed-period management aggregates; export and HTML use the identical disclosed cells."""

import csv
from datetime import date, timedelta
from io import BytesIO, StringIO

from django.core.exceptions import PermissionDenied
from django.db.models import Count, Exists, OuterRef, Q
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from casework.models import Event, Execution
from casework.views import _attachment, record_access
from matters.models import Status
from matters.selectors import aggregate_scope
from specialists.models import CorrectiveAction, Hearing, Licence, OpinionIssue

MINIMUM = 3
HIDDEN = '—'


def _release_partition(counts):
    """Conceal small non-zero cells and a complement; do not publish the grand total here."""
    redacted = {key for key, count in counts.items() if 0 < count < MINIMUM}
    if redacted:
        companions = [key for key, count in counts.items() if count >= MINIMUM]
        if companions:
            redacted.add(max(companions, key=lambda key: counts[key]))
    return {key: HIDDEN if key in redacted else count for key, count in counts.items()}


def _cohort(policy, year):
    return aggregate_scope(policy).filter(
        opened_at__date__gte=date(year, 1, 1),
        opened_at__date__lte=min(date(year, 12, 31), timezone.localdate()),
    )


def context(request):
    policy = request.policy
    if not policy.has('reports.aggregate'):
        raise PermissionDenied
    today = timezone.localdate()
    years = (today.year, today.year - 1)
    # No ad-hoc ranges, department, matter IDs or query-driven cohort drill-through.
    raw = request.GET.get('year', str(years[0]))
    valid_years = {str(year): year for year in years}
    if raw not in valid_years:
        raise Http404
    year = valid_years[raw]
    qs = _cohort(policy, year)
    issued = Event.objects.filter(case_id=OuterRef('pk'), command__in=('issue', 'sp_lit_outcome'))
    completed = qs.filter(
        Q(status=Status.COMPLETED) | Q(status=Status.CLOSED, workflow__isnull=False) & Q(Exists(issued))
    ).count()
    total = qs.count()
    other = total - completed
    masked = 0 < completed < MINIMUM or 0 < other < MINIMUM
    values = (total, completed, other) if not masked else (HIDDEN,) * 3
    metrics = [
        dict(label=label, value=value, icon=icon, tone=tone)
        for (label, icon, tone), value in zip(
            (
                (_('Reportable matters'), 'briefcase', 'blue'),
                (_('Completed'), 'check-circle', 'green'),
                (_('Other outcomes and open matters'), 'clock', 'amber'),
            ),
            values,
            strict=True,
        )
    ]
    groups = (
        (_('Contracts and MoUs'), ('contract', 'mou')),
        (_('Litigation'), ('litigation', 'defamation')),
        (_('Legal opinions'), ('opinion',)),
        (_('IP and broadcasting rights'), ('ip', 'copyright', 'broadcasting')),
        (_('Regulatory compliance'), ('compliance',)),
    )
    categories = {
        row['category_id']: row['total']
        for row in qs.order_by().values('category_id').annotate(total=Count('pk'))
    }
    module_totals = {label: sum(categories.get(code, 0) for code in codes) for label, codes in groups}
    module_totals[_('Other matters')] = total - sum(module_totals.values())
    # If the status partition was masked, other sections don't disclose its total by summation.
    modules = list(
        (
            _release_partition(module_totals) if not masked else dict.fromkeys(module_totals, HIDDEN)
        ).items()
    )
    months = dict.fromkeys(range(1, 13), 0)
    for row in qs.order_by().values('opened_at__month').annotate(total=Count('pk')):
        months[row['opened_at__month']] = row['total']
    month_values = _release_partition(months) if not masked else dict.fromkeys(months, HIDDEN)
    timeline = [(date(year, month, 1), count) for month, count in month_values.items()]
    cohort = qs.order_by().values('pk')
    soon = today + timedelta(days=30)
    indicators = (
        (
            _('Matters with a hearing in the next 30 days'),
            Hearing.objects.filter(
                litigation_id__in=cohort,
                status=Hearing.Status.SCHEDULED,
                starts_at__gte=timezone.now(),
                starts_at__date__lte=soon,
            )
            .order_by()
            .values('litigation_id')
            .distinct()
            .count(),
        ),
        (
            _('Issued opinions'),
            OpinionIssue.objects.filter(version__opinion_id__in=cohort)
            .order_by()
            .values('version__opinion_id')
            .distinct()
            .count(),
        ),
        (
            _('Executed contract matters'),
            Execution.objects.filter(version__contract_id__in=cohort)
            .order_by()
            .values('version__contract_id')
            .distinct()
            .count(),
        ),
        (
            _('Matters with rights expiring in the next 30 days'),
            Licence.objects.filter(portfolio_id__in=cohort, ends_on__range=(today, soon))
            .order_by()
            .values('portfolio_id')
            .distinct()
            .count(),
        ),
        (
            _('Matters with overdue corrective actions'),
            CorrectiveAction.objects.filter(assessment__obligation_id__in=cohort, due_on__lt=today)
            .exclude(remediations__decision__verified=True)
            .order_by()
            .values('assessment__obligation_id')
            .distinct()
            .count(),
        ),
    )
    operational = [
        (label, value if value == 0 or value >= MINIMUM else HIDDEN) for label, value in indicators
    ]
    return {
        'metrics': metrics,
        'modules': modules,
        'timeline': timeline,
        'operational': operational,
        'year': year,
        'years': years,
        'as_of': today,
        'masked': masked,
    }


def report_rows(report):
    yield _('Metric'), _('Value')
    for metric in report['metrics']:
        yield metric['label'], metric['value']
    yield _('Matters opened by module'), ''
    yield from report['modules']
    yield _('Monthly intake'), ''
    for month, value in report['timeline']:
        yield month.strftime('%Y-%m'), value
    yield _('Operational indicators'), ''
    yield from report['operational']


def export(request, format_):
    if format_ not in ('csv', 'pdf', 'xlsx'):
        raise Http404
    values = context(request)
    rows = list(report_rows(values))
    record_access(request, f'management_export_{format_}', str(values['year']))
    title = f'TBC LSMS · {_("Management overview")} · {values["year"]}'
    if format_ == 'csv':
        output = StringIO(newline='')
        writer = csv.writer(output)
        writer.writerows(
            [
                [_('Reporting year'), values['year']],
                [_('As of (EAT)'), values['as_of'].isoformat()],
                *rows,
            ]
        )
        return _attachment(
            output.getvalue().encode('utf-8-sig'),
            f'lsms-management-{values["year"]}.csv',
            'text/csv; charset=utf-8',
        )
    if format_ == 'xlsx':
        return _attachment(
            _xlsx(values, rows),
            f'lsms-management-{values["year"]}.xlsx',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    from xml.sax.saxutils import escape

    output = BytesIO()
    styles = getSampleStyleSheet()
    table = Table(
        [[Paragraph(escape(str(k)), styles['BodyText']), str(v)] for k, v in rows],
        colWidths=[400, 70],
        repeatRows=1,
    )
    headers = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dceaff')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f2f6fc')]),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
    ]
    table.setStyle(TableStyle(headers))
    document = SimpleDocTemplate(
        output, pagesize=A4, title=title, leftMargin=50, rightMargin=50, topMargin=40, bottomMargin=36
    )
    document.build(
        [
            Paragraph(escape(title), styles['Title']),
            Paragraph(
                escape(f'{_("Internal use only")} · {_("As of (EAT)")}: {values["as_of"].isoformat()}'),
                styles['BodyText'],
            ),
            Spacer(1, 12),
            table,
        ]
    )
    return _attachment(output.getvalue(), f'lsms-management-{values["year"]}.pdf', 'application/pdf')


def _xlsx(report, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    book = Workbook()
    sheet = book.active
    sheet.title = str(_('Management overview'))
    sheet.append([_('Reporting year'), report['year']])
    sheet.append([_('As of (EAT)'), report['as_of'].isoformat()])
    for label, value in rows:
        sheet.append([str(label), value])
    # Even future labels must stay inert; only aggregate numbers are numeric cells.
    for cells in sheet.iter_rows():
        for cell in cells:
            if isinstance(cell.value, str):
                cell.data_type = 's'
            if cell.row == 3:
                cell.font = Font(color='FFFFFF', bold=True)
                cell.fill = PatternFill('solid', fgColor='244EA2')
            if cell.column == 1:
                cell.alignment = Alignment(wrap_text=True)
    sheet.column_dimensions['A'].width = 62
    sheet.column_dimensions['B'].width = 20
    sheet.freeze_panes = 'A4'
    output = BytesIO()
    book.save(output)
    return output.getvalue()
