"""Repository byte/metadata separation, export parity, and scoped deadline delivery."""

import csv
from datetime import UTC, datetime, time, timedelta
from io import BytesIO, StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from pypdf import PdfReader

from casework.models import Document
from identity.models import AuditLog, RoleCode, RoleGrant
from matters.models import Classification, LegalAssignment, LegalMatter, Milestone, Status
from specialists.models import DeadlineNotice
from specialists.reminders import send_due
from workspace.reporting import report_rows

from .factories import authenticate_as
from .test_casework import file, pdf_bytes
from .test_specialists import SpecialistFixture


class LibraryTests(SpecialistFixture):
    def test_sealed_scope_download_recheck_and_no_generic_document_bypass(self):
        case = self.docket()
        item = self.repository_item(
            case, sealed=True, name='PRIVILEGED ITEM TITLE', filename='sealed-secret.pdf'
        )
        row = item.versions.get()
        url = reverse('library_download', args=[item.pk, row.pk])
        detail = reverse('library_detail', args=[item.pk])
        index = reverse('library_index')
        sr = authenticate_as(self.records)
        self.assertContains(self.review.get(detail), 'PRIVILEGED ITEM TITLE')
        self.assertEqual(self.sen.get(detail).status_code, 404)
        self.assertEqual(sr.get(detail).status_code, 404)
        self.assertEqual(self.sen.get(url).status_code, 404)
        self.assertEqual(sr.get(url).status_code, 403)
        self.assertEqual(self.portal.get(url).status_code, 403)
        self.assertEqual(authenticate_as(self.ict).get(url).status_code, 403)
        self.assertEqual(
            self.review.get(reverse('legal_document', args=[case.pk, row.document_id])).status_code, 404
        )
        self.assertNotContains(
            self.review.get(reverse('matter_detail', args=[case.pk])), 'sealed-secret.pdf'
        )
        self.assertNotContains(self.review.get(index, {'view': 'evidence'}), 'sealed-secret.pdf')
        self.assertEqual(
            self.sen.get(index, {'q': 'sealed-secret.pdf'}).context['page_obj'].paginator.count, 0
        )
        self.assertEqual(sr.get(index, {'q': item.reference}).context['page_obj'].paginator.count, 0)
        result = self.lead.get(url)
        self.assertTrue(result.content.startswith(b'%PDF-'))
        self.assertEqual(result.headers['Cache-Control'], 'no-store, private')
        self.assertIn('attachment;', result.headers['Content-Disposition'])
        self.assertTrue(
            AuditLog.objects.filter(action='library_version_downloaded', target=str(row.pk)).exists()
        )
        LegalAssignment.objects.filter(matter_id=case.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.review.get(url).status_code, 404)
        LegalMatter.objects.filter(pk=case.pk).update(classification=Classification.CONFIDENTIAL)
        self.assertEqual(self.lead.get(url).status_code, 404)
        self.permit(case, self.head)
        self.assertEqual(self.lead.get(url).status_code, 200)

    def test_version_quarantine_custody_and_safe_metadata(self):
        case = self.docket()
        index = reverse('library_index')
        records = authenticate_as(self.records)
        item = self.repository_item(case, name='PRIVATE TITLE', filename='v1-original.pdf')
        first = item.versions.get()
        detail = reverse('library_detail', args=[item.pk])
        make = reverse('library_action', args=[item.pk, 'version'])
        hold = reverse('library_action', args=[item.pk, 'hold'])
        lift = reverse('library_action', args=[item.pk, 'lift'])
        archive = reverse('library_action', args=[item.pk, 'archive'])
        recheck = reverse('library_action', args=[item.pk, 'rescan'])
        page = records.get(detail)
        self.assertContains(page, item.reference)
        for text in (
            item.title,
            first.document.name,
            first.provenance,
            item.tags,
            first.document.checksum,
        ):
            self.assertNotContains(page, text)
        self.assertEqual(
            records.get(index, {'q': first.document.name}).context['page_obj'].paginator.count, 0
        )
        self.assertEqual(records.get(index, {'view': 'evidence'}).status_code, 403)
        self.assertContains(self.review.get(index, {'q': first.document.name}), item.title)
        self.assertEqual(authenticate_as(self.outsider).get(detail).status_code, 404)
        self.assertEqual(records.get(make).status_code, 403)
        self.post(records, hold, case, reason='PRIVATE HOLD REASON')
        self.assertNotContains(records.get(detail), 'PRIVATE HOLD REASON')
        self.post(records, archive, case, want=403, reason='Cannot archive on hold')
        self.post(records, lift, case, want=403, reason='Cannot override')
        self.post(self.lead, lift, case, reason='Independent Head review')
        second_bytes = pdf_bytes('Second immutable repository version')
        with patch('casework.storage.scan_bytes', return_value=('pending', 'not-available')):
            self.post(
                self.review,
                make,
                case,
                provenance='QUARANTINED PROVENANCE',
                document=file('v2-pending.pdf', second_bytes),
            )
        second = item.versions.get(number=2)
        second_url = reverse('library_download', args=[item.pk, second.pk])
        first_url = reverse('library_download', args=[item.pk, first.pk])
        self.assertEqual(self.review.get(second_url).status_code, 404)
        self.post(records, archive, case, want=400, reason='Quarantine not resolved')
        self.assertContains(self.review.get(detail), 'Quarantined')
        with patch('library.services.scan_bytes', return_value=('pending', 'offline')):
            self.post(self.review, recheck, case, want=400, pending=second.pk)
        with patch('library.services.scan_bytes', return_value=('clean', 'verified-test')):
            self.post(self.review, recheck, case, pending=second.pk)
        self.assertEqual(self.review.get(second_url).content, second_bytes)
        self.assertNotEqual(self.review.get(first_url).content, second_bytes)
        self.post(records, archive, case, reason='Preserve, not dispose')
        self.post(self.review, make, case, want=403, provenance='Post archive', document=file())
        self.assertEqual(self.review.get(first_url).status_code, 200)
        Document.objects.filter(pk=second.document_id).update(checksum='0' * 64)
        self.assertEqual(self.review.get(second_url).status_code, 503)
        self.assertEqual(item.versions.count(), 2)
        self.assertEqual(item.custody.count(), 3)


class ReportingTests(SpecialistFixture):
    def fixture(self, reference, *, category='opinion', status=Status.REVIEW, opened_at=None, **attrs):
        return LegalMatter.objects.create(
            reference=reference,
            title=f'Private {reference}',
            department=self.unit,
            category_id=category,
            status=status,
            opened_at=opened_at or timezone.now(),
            **attrs,
        )

    def test_fixed_cohort_exports_parity_and_small_group_complement(self):
        for no in range(3):
            self.fixture(f'NEVER-EXPORTED-{no}', category='opinion', status=Status.COMPLETED)
            self.fixture(
                f'SENSITIVE-NOT-REPORTABLE-{no}',
                category='compliance',
                classification=Classification.CONFIDENTIAL,
            )
            self.fixture(f'DEMO-NOT-REPORTABLE-{no}', is_demo=True)
        for no in range(4):
            self.fixture(f'COURT-NEVER-EXPORTED-{no}', category='litigation')
        self.fixture('SINGLE-SUPPRESSED-RIGHTS', category='copyright')
        prior = timezone.make_aware(
            datetime.combine(self.today.replace(month=1, day=1) - timedelta(days=1), time(12))
        )
        for no in range(3):
            self.fixture(f'PRIOR-YEAR-{no}', opened_at=prior, category='contract')
        manager = authenticate_as(self.manager)
        url = reverse('management_dashboard')
        page = manager.get(url, {'department': self.other_unit.pk, 'q': 'Private'})
        self.assertEqual([row['value'] for row in page.context['metrics']], [8, 3, 5])
        modules = {str(k): v for k, v in page.context['modules']}
        self.assertEqual(modules['IP and broadcasting rights'], '—')
        self.assertEqual(modules['Litigation'], '—')
        self.assertEqual(modules['Legal opinions'], 3)
        for value in (
            'COURT-NEVER-EXPORTED',
            'SENSITIVE-NOT-REPORTABLE',
            'SINGLE-SUPPRESSED-RIGHTS',
            'PRIOR-YEAR-',
        ):
            self.assertNotContains(page, value)
        last = manager.get(url, {'year': self.today.year - 1})
        self.assertEqual([row['value'] for row in last.context['metrics']], [3, 0, 3])
        self.assertEqual(manager.get(url, {'year': self.today.year - 2}).status_code, 404)
        self.assertEqual(self.lead.get(url).status_code, 403)
        expected = [(str(label), str(value)) for label, value in report_rows(page.context)]
        for format_ in ('csv', 'xlsx', 'pdf'):
            download = reverse('management_export', args=[format_])
            result = manager.get(download)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers['Cache-Control'], 'no-store, private')
            self.assertIn('attachment;', result.headers['Content-Disposition'])
            self.assertEqual(self.portal.get(download).status_code, 403)
            self.assertEqual(manager.get(download, {'year': 'all'}).status_code, 404)
            if format_ == 'csv':
                rows = list(csv.reader(StringIO(result.content.decode('utf-8-sig'))))[2:]
                self.assertEqual([tuple(row) for row in rows], expected)
            elif format_ == 'xlsx':
                sheet = load_workbook(BytesIO(result.content), read_only=True).active
                rows = [(str(a or ''), '' if b is None else str(b)) for a, b in list(sheet.values)[2:]]
                self.assertEqual(rows, expected)
                self.assertFalse(any(cell.data_type == 'f' for row in sheet.iter_rows() for cell in row))
            else:
                text = ' '.join(p.extract_text() for p in PdfReader(BytesIO(result.content)).pages)
                self.assertIn('Operational indicators', text)
                self.assertNotIn('SINGLE-SUPPRESSED-RIGHTS', text)
        self.assertEqual(manager.get(reverse('library_index')).status_code, 403)
        RoleGrant.objects.filter(user=self.manager, role_id=RoleCode.MANAGEMENT).update(
            revoked_at=timezone.now()
        )
        self.assertEqual(manager.get(reverse('management_export', args=['csv'])).status_code, 403)

    def test_operational_suppression_counts_distinct_cases_not_repeated_rows(self):
        case = self.docket()
        due = self.today + timedelta(days=1)
        for hour in range(9, 12):
            self.sp(
                case,
                'litigation',
                'lit_hearing',
                self.review,
                starts_at=f'{due.isoformat()}T{hour:02d}:30',
                room='A',
                purpose='Synthetic',
            )
        view = authenticate_as(self.manager).get(reverse('management_dashboard'))
        self.assertEqual(
            dict(view.context['operational'])['Matters with a hearing in the next 30 days'], '—'
        )


class DeadlineTests(SpecialistFixture):
    def test_eat_windows_dedupe_escalation_and_live_visibility(self):
        # 21:15 UTC is the next day at 00:15 in East Africa.
        now = datetime.combine(self.today, time(21, 15), tzinfo=UTC)
        today_eat = timezone.localdate(now)
        self.assertEqual(today_eat, self.today + timedelta(days=1))
        case = self.docket()
        self.sp(
            case,
            'litigation',
            'lit_hearing',
            self.review,
            starts_at=f'{today_eat.isoformat()}T09:15',
            room='Court room',
            purpose='Private hearing',
        )
        hearing = case.litigation.hearings.get()
        self.assertEqual(send_due(demo=False, now=now), 1)
        self.assertEqual(send_due(demo=False, now=now), 0)
        row = DeadlineNotice.objects.get()
        self.assertEqual(row.window, DeadlineNotice.Window.TODAY)
        self.assertEqual(row.recipient, self.author)
        self.assertNotIn('Court room', row.notification.body)
        self.assertEqual(send_due(demo=False, now=now + timedelta(days=1)), 2)
        self.assertEqual(
            set(DeadlineNotice.objects.filter(window='overdue').values_list('recipient_id', flat=True)),
            {self.senior.pk, self.head.pk},
        )
        notice_clock = Mock(wraps=timezone)
        notice_clock.localdate.side_effect = lambda moment=None: timezone.localdate(moment or now)
        with patch('specialists.reminders.timezone', notice_clock):
            self.assertContains(
                self.review.get(reverse('inbox')), 'An authorised legal deadline is due today'
            )
            self.sp(
                case,
                'litigation',
                'lit_hearing_result',
                self.review,
                hearing=hearing.pk,
                hearing_status='cancelled',
                reason='Cancelled',
            )
            self.assertNotContains(
                self.review.get(reverse('inbox')), 'An authorised legal deadline is due today'
            )
        self.assertEqual(send_due(demo=False, now=now + timedelta(days=2)), 0)
        self.assertTrue(Milestone.objects.get(pk=hearing.milestone_id).is_cancelled)
        output = StringIO()
        call_command('deliver_legal_reminders', stdout=output)
        self.assertIn('Delivered 0', output.getvalue())

    def test_assignment_revocation_and_named_clearance_remove_existing_notice(self):
        case = self.docket()
        milestone = Milestone.objects.create(
            matter_id=case.pk, kind=Milestone.Kind.COMPLIANCE, date=self.due
        )
        self.assertEqual(send_due(demo=False), 1)
        inbox = reverse('inbox')
        self.assertContains(self.review.get(inbox), 'An authorised legal deadline is approaching')
        LegalMatter.objects.filter(pk=case.pk).update(classification=Classification.CONFIDENTIAL)
        self.assertNotContains(self.review.get(inbox), 'An authorised legal deadline is approaching')
        case.refresh_from_db()
        self.permit(case, self.author)
        self.assertContains(self.review.get(inbox), 'An authorised legal deadline is approaching')
        LegalAssignment.objects.filter(matter_id=case.pk).update(revoked_at=timezone.now())
        self.assertNotContains(self.review.get(inbox), 'An authorised legal deadline is approaching')
        self.assertEqual(DeadlineNotice.objects.get().milestone, milestone)
