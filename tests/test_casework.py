"""Phase 2 HTTP workflow contract, evidence safety and authorization regressions. Revisions: Git."""

import hashlib
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pyotp
from cryptography.fernet import Fernet
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from casework import checklist, services
from casework.models import (
    Case,
    Checklist,
    Contract,
    Decision,
    Document,
    Event,
    Execution,
    Release,
    ReviewRound,
)
from identity.models import AuditLog, RoleCode, RoleGrant, TotpDevice
from matters.models import (
    Classification,
    LegalAssignment,
    LegalMatter,
    LegalRequest,
    MatterAccess,
    Milestone,
    StaffStatus,
    Status,
)

from .factories import authenticate_as, division, employee, grant

DOCUMENT_KEY = Fernet.generate_key().decode()
MFA_KEY = Fernet.generate_key().decode()


def pdf_bytes(text='Synthetic test draft'):
    output = BytesIO()
    page = canvas.Canvas(output)
    page.drawString(48, 750, text)
    page.save()
    return output.getvalue()


def file(name='draft-review.pdf', content=None):
    return SimpleUploadedFile(
        name, content if content is not None else pdf_bytes(), content_type='application/octet-stream'
    )


@override_settings(
    ENV='test', LOCAL_AUTH_ENABLED=True, DOCUMENT_KEY=DOCUMENT_KEY, MFA_KEY=MFA_KEY, CLAMD_SOCKET=''
)
class CaseworkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.department, cls.other_department = division('DLSA'), division('DLSB')
        cls.employee = employee('staff', cls.department)
        cls.other = employee('peer', cls.department)
        cls.hod = employee('hod', cls.department, roles=(RoleCode.HOD, RoleCode.STAFF))
        cls.senior = employee('senior', cls.department, roles=(RoleCode.SENIOR,))
        cls.other_senior = employee('senior2', cls.other_department, roles=(RoleCode.SENIOR,))
        cls.officer = employee('reviewer', cls.department, roles=(RoleCode.OFFICER,))
        cls.officer2 = employee('reviewer2', cls.other_department, roles=(RoleCode.OFFICER,))
        cls.head = employee('head', cls.department, roles=(RoleCode.HEAD,))
        cls.ict = employee('ict', roles=(RoleCode.ICT,))
        cls.secret = pyotp.random_base32()
        TotpDevice.objects.create(
            user=cls.head, encrypted_secret=Fernet(MFA_KEY.encode()).encrypt(cls.secret.encode())
        )

    def setUp(self):
        self.now = timezone.now()
        clock = patch('identity.security.timezone', wraps=timezone)
        clock.start().now.return_value = self.now
        self.addCleanup(clock.stop)
        self.day = timezone.localdate() + timedelta(days=14)
        self.staff = authenticate_as(self.employee)
        self.triage = authenticate_as(self.senior)
        self.reviewer = authenticate_as(self.officer)
        self.signer = authenticate_as(self.head)

    def _submission(self, *, contract=False, **kwargs):
        data = {
            'submission_key': uuid4(),
            'title': 'Synthetic commissioning request',
            'category': 'contract' if contract else 'advice',
            'office': 'Programme office',
            'contact': 'staff@test.example.invalid',
            'summary': 'Synthetic background from the requester.',
            'required_on': self.day.isoformat(),
            'priority': 'normal',
            'declaration': 'on',
        }
        if contract:
            data |= {
                'counterparty': 'Synthetic Media Company',
                'contract_type': 'content',
                'amount': '2400000.00',
                'currency': 'tzs',
                'duration': 'Six months',
                'draft': file(),
            }
        return data | kwargs

    def _submit(self, *, contract=False, **kwargs):
        response = self.staff.post(reverse('new_request'), self._submission(contract=contract, **kwargs))
        self.assertEqual(response.status_code, 302, getattr(response, 'context', ''))
        return Case.objects.get(pk=response.url.rstrip('/').split('/')[-1])

    def _action(self, case, command, client, expected=302, **data):
        case.refresh_from_db()
        url = reverse('case_action', args=[case.pk, command])
        response = client.post(
            url, {'revision': case.revision, 'reason': 'DLS PRIVATE: rational basis', **data}
        )
        self.assertEqual(response.status_code, expected, getattr(response.context, 'dicts', ''))
        case.refresh_from_db()
        return response

    def _start(self, case):
        self._action(case, 'triage', self.triage)
        self._action(case, 'accept', self.triage, verified='on')
        self._action(
            case,
            'assign',
            self.triage,
            officer=self.officer.pk,
            due_on=self.day.isoformat(),
            priority='high',
        )
        self._action(case, 'begin', self.reviewer)
        return services.latest_version(case)

    def _freeze(self, case, expected=302, **kwargs):
        self._action(
            case,
            'submit_review',
            self.reviewer,
            expected,
            **(
                {
                    'recommendation': 'approve',
                    'response': 'This is the approved response for the requester.',
                    'confirmed': 'on',
                }
                | kwargs
            ),
        )
        return case.reviews.first()

    def _package_data(self, review):
        return {'review_id': review.pk, 'package_digest': review.digest}

    def _decision(self, case, review, *, outcome='clear', expected=302, **kwargs):
        return self._action(
            case,
            'decide',
            self.signer,
            expected,
            **(
                self._package_data(review)
                | {
                    'outcome': outcome,
                    'gates_verified': 'on',
                    'no_conflict': 'on',
                    'code': pyotp.TOTP(self.secret).at(self.now),
                }
                | kwargs
            ),
        )

    def _part(self, case, version, code, data, *, client=None, expected=302):
        case.refresh_from_db()
        url = reverse('checklist_section', args=[case.pk, version.pk, code])
        response = (client or self.reviewer).post(url, {'revision': case.revision, **data})
        self.assertEqual(response.status_code, expected, getattr(response.context, 'dicts', ''))
        case.refresh_from_db()
        return response

    def _fill(self, case, version, *, risk='medium'):
        for section in checklist.REVIEW_SECTIONS:
            data = {f'{key}_choice': 'yes' for key, _ in section.items}
            if section.code == 'I':
                data |= {
                    'I-01_evidence': str(version.draft_id),
                    'I-03_choice': 'no',
                    'I-03_remarks': 'Verified comparable evidence.',
                    'I-08_choice': 'na',
                    'I-08_remarks': 'No prior correspondence in this synthetic matter.',
                }
            self._part(case, version, section.code, data)
        self._part(
            case,
            version,
            'XI',
            {
                'risk_class': risk,
                'risk_0_category': 'Rights',
                'risk_0_description': 'Synthetic risk',
                'risk_0_likelihood': 'low',
                'risk_0_impact': 'medium',
                'risk_0_mitigation': 'Verify rights schedule.',
                'risk_0_responsible': 'Content office',
            },
        )

    def test_employee_information_assignment_review_head_and_issue_with_scoped_notifications(self):
        case = self._submit(attachment=file('employee.pdf'))
        track = reverse('staff_request', args=[case.pk])
        legal = reverse('matter_detail', args=[case.pk])
        self.assertContains(self.staff.get(track), 'Your request has been received')
        self.assertNotContains(
            authenticate_as(self.other_senior).get(reverse('inbox')),
            'New request for preliminary review',
        )
        self.assertContains(self.triage.get(reverse('inbox')), 'New request for preliminary review')
        self.assertContains(self.triage.get(reverse('inbox')), legal)
        self._action(case, 'triage', self.triage)
        self._action(
            case,
            'ask',
            self.triage,
            public_message='Please confirm the delivery location.',
            due_on=self.day.isoformat(),
        )
        tracked = self.staff.get(track)
        self.assertContains(tracked, 'Please confirm the delivery location.')
        self.assertNotContains(tracked, 'DLS PRIVATE')
        reply_url = reverse('case_reply', args=[case.pk])
        self.assertEqual(
            authenticate_as(self.other)
            .post(reply_url, {'revision': case.revision, 'reply': 'peer'})
            .status_code,
            404,
        )
        self.assertEqual(
            self.staff.post(
                reply_url, {'revision': case.revision, 'reply': 'Synthetic headquarters.'}
            ).status_code,
            302,
        )
        self.assertEqual(LegalMatter.objects.get(pk=case.pk).status, Status.INFORMATION)
        self._action(case, 'verify', self.triage)
        self._action(case, 'accept', self.triage, verified='on')
        self._action(
            case,
            'assign',
            self.triage,
            officer=self.officer.pk,
            due_on=self.day.isoformat(),
            priority='high',
        )
        self.assertContains(self.reviewer.get(reverse('inbox')), 'Legal work assigned to you')
        self.assertEqual(LegalAssignment.objects.get(matter_id=case.pk).due_on, self.day)
        self._action(case, 'begin', self.reviewer)
        self._action(case, 'note', self.reviewer, reason='PRIVATE STRATEGY MUST NOT LEAK')
        self._action(
            case, 'upload', self.reviewer, kind='internal', document=file('internal-evidence.pdf')
        )
        self._action(case, 'upload', self.reviewer, kind='response', document=file('approved-memo.pdf'))
        memo = case.documents.get(kind='response')
        internal = case.documents.get(kind='internal')
        review = self._freeze(case, deliverables=str(memo.pk))
        self.assertContains(
            self.signer.get(reverse('inbox')), 'A frozen review is awaiting a Head decision'
        )
        self.assertEqual(
            self.staff.get(reverse('case_review', args=[case.pk, review.pk])).status_code, 403
        )
        self.assertNotContains(self.staff.get(track), 'approved-memo.pdf')
        self._decision(case, review)
        self.assertEqual(case.matter.status, Status.APPROVED)
        self.assertEqual(LegalRequest.objects.get(pk=case.pk).public_status, StaffStatus.PROCESSING)
        self.assertFalse(Release.objects.exists())
        self.assertNotContains(self.staff.get(track), 'This is the approved response')
        self._action(case, 'issue', self.signer, **self._package_data(review), confirmed='on')
        self.assertEqual(case.matter.status, Status.COMPLETED)
        self.assertEqual(Release.objects.get().document, memo)
        self.assertContains(self.staff.get(track), 'This is the approved response for the requester.')
        self.assertContains(self.staff.get(track), 'approved-memo.pdf')
        self.assertNotContains(self.staff.get(track), 'PRIVATE STRATEGY')
        self.assertContains(
            self.staff.get(reverse('inbox')), 'Your approved legal response is available'
        )
        api = self.staff.get(reverse('api_staff_requests')).content.decode()
        for hidden in (
            'DLS PRIVATE',
            'PRIVATE STRATEGY',
            'internal-evidence.pdf',
            'head_review',
            'draft-review.pdf',
        ):
            self.assertNotIn(hidden, api)
        get = lambda doc: reverse('staff_document', args=[case.pk, doc.pk])  # noqa: E731
        self.assertEqual(self.staff.get(get(internal)).status_code, 404)
        authorized = self.staff.get(get(memo))
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(hashlib.sha256(authorized.content).hexdigest(), memo.checksum)
        self.assertIn('attachment;', authorized.headers['Content-Disposition'])
        self.assertEqual(authorized.headers['Cache-Control'], 'no-store, private')
        self.assertEqual(authenticate_as(self.hod).get(get(memo)).status_code, 404)
        self.assertEqual(authenticate_as(self.other).get(get(memo)).status_code, 404)
        audit = AuditLog.objects.filter(target=str(case.pk)).values_list('detail', flat=True)
        self.assertNotIn('PRIVATE STRATEGY', str(list(audit)))
        self._action(case, 'close', self.triage, retention_confirmed='on')
        self.assertEqual(case.matter.status, Status.CLOSED)
        self.assertEqual(Decision.objects.get().outcome, Decision.Outcome.CLEAR)

    def test_checklist_preserves_order_and_all_parts_through_approval_pdf_and_execution(self):
        case = self._submit(contract=True)
        version = self._start(case)
        detail = reverse('contract_detail', args=[case.pk])
        page = self.reviewer.get(detail)
        self.assertContains(page, 'TBC/DLS/CRF/001/2026')
        self.assertContains(page, 'Draft digital checklist')
        self.assertContains(page, version.contract.reference)
        self.assertContains(page, 'XIII. Post-Execution Requirements')
        self.assertEqual(
            [len(s.items) for s in checklist.SECTIONS], [0, 8, 7, 8, 7, 8, 8, 8, 8, 10, 7, 0, 0, 6]
        )
        self.assertEqual(len(checklist.REVIEW_IDS | checklist.POST_IDS), 85)
        self._part(case, version, 'XIII', {}, expected=403)
        self._freeze(case, expected=400)
        self.assertEqual(case.reviews.count(), 0)
        self._fill(case, version)
        self.assertContains(self.reviewer.get(detail), '79 / 79')
        review = self._freeze(case, release_draft='on')
        self.assertEqual(review.version, version)
        self.assertEqual(len(review.package['contract']['answers']), 79)
        self.assertNotIn('XIII-01', review.package['contract']['answers'])
        self.assertEqual(review.package['contract']['answers']['I-08']['choice'], 'na')
        self.assertEqual(review.package['contract']['answers']['I-03']['choice'], 'no')
        self.assertEqual(
            review.package['contract']['answers']['I-01']['evidence'], [str(version.draft_id)]
        )
        self.assertEqual(services.digest(review.package), review.digest)
        self.assertEqual(review.package['contract']['identity']['value'], '2400000.00 TZS')
        self._part(case, version, 'I', {'I-01_choice': 'no'}, expected=403)
        with self.assertRaises(PermissionDenied):
            ReviewRound.objects.filter(pk=review.pk).update(package={})
        with self.assertRaises(PermissionDenied):
            version.save()
        self._decision(case, review)
        self.assertEqual(case.matter.status, Status.APPROVED)
        self.assertFalse(Execution.objects.exists())
        self.assertFalse(Release.objects.exists())
        self.assertFalse(Checklist.objects.filter(kind=Checklist.Kind.POST).exists())
        pdf = reverse('checklist_export', args=[case.pk, review.pk])
        exported = self.signer.get(pdf)
        self.assertEqual(exported.status_code, 200)
        self.assertTrue(exported.content.startswith(b'%PDF-'))
        text = ' '.join(p.extract_text() for p in PdfReader(BytesIO(exported.content)).pages)
        for marker in [
            review.digest,
            version.draft.checksum,
            'III. Legal and Regulatory',
            'XI. Risk Classification',
            'XII. Approval and Legal Clearance',
            'XIII. Post-Execution Requirements',
            'Execution not recorded',
        ]:
            self.assertIn(marker, text)
        self.assertEqual(self.staff.get(pdf).status_code, 403)
        self._action(case, 'issue', self.signer, **self._package_data(review), confirmed='on')
        today = timezone.localdate().isoformat()
        execution_data = self._package_data(review) | {
            'signatories': 'Fictional authorised representatives',
            'signed_on': today,
            'effective_on': today,
            'expires_on': self.day.isoformat(),
            'gates_verified': 'on',
            'prerequisites': 'Verified synthetic authority file EXE-01.',
            'text_matches': 'on',
        }
        # Login/approval's used TOTP step cannot be reused for execution.
        self._action(
            case,
            'execute',
            self.signer,
            expected=400,
            document=file('signed.pdf'),
            code=pyotp.TOTP(self.secret).at(self.now),
            **execution_data,
        )
        self.assertFalse(Document.objects.filter(kind=Document.Kind.EXECUTION).exists())
        code = pyotp.TOTP(self.secret).at(self.now + timedelta(seconds=30))
        self._action(
            case, 'execute', self.signer, document=file('signed.pdf'), code=code, **execution_data
        )
        execution = Execution.objects.get()
        self.assertEqual(execution.clearance, Decision.objects.get())
        self.assertEqual(execution.version, version)
        self.assertEqual(Milestone.objects.get(matter_id=case.pk).date, self.day)
        self._part(case, version, 'XIII', {'finalize': 'on'}, expected=400)
        self._part(
            case,
            version,
            'XIII',
            {f'{key}_choice': 'yes' for key in checklist.POST_IDS}
            | {
                'XIII-01_evidence': str(execution.signed_copy_id),
                'manager': 'Content custodian',
                'monitoring': 'Monthly',
                'archive': 'Reference A-001',
                'finalize': 'on',
            },
        )
        post = Checklist.objects.get(version=version, kind=Checklist.Kind.POST)
        self.assertEqual(len(post.answers), 6)
        self.assertEqual(post.finalized_by, self.officer)
        self._part(case, version, 'XIII', {}, expected=403)
        self.assertEqual(ReviewRound.objects.get().digest, review.digest)
        self.assertEqual(
            self.staff.get(
                reverse('staff_document', args=[case.pk, execution.signed_copy_id])
            ).status_code,
            404,
        )
        text = ' '.join(p.extract_text() for p in PdfReader(BytesIO(self.signer.get(pdf).content)).pages)
        self.assertIn('Reference A-001', text)
        self.assertIn('XIII-06', text)
        self.assertNotIn('Execution not recorded', text)

    def test_conditional_is_not_executable_reopen_requires_new_version_and_review(self):
        case = self._submit(contract=True)
        version = self._start(case)
        self._fill(case, version)
        first = self._freeze(
            case, recommendation='conditional', amendments='Clarify the rights schedules.'
        )
        self._decision(case, first, expected=400)  # Head cannot approve a conditional recommendation.
        self._decision(
            case,
            first,
            outcome='conditional',
            conditions='Verify amended terms.',
            condition_due=self.day.isoformat(),
        )
        self.assertEqual(case.matter.status, Status.REVIEW)
        self.assertEqual(Decision.objects.get().condition_owner, self.officer)
        self.assertEqual(
            self.signer.get(reverse('case_action', args=[case.pk, 'execute'])).status_code, 403
        )
        self.assertEqual(
            self.signer.get(reverse('case_action', args=[case.pk, 'issue'])).status_code, 403
        )
        self._freeze(case, expected=400)  # Conditions cannot be ignored.
        self._action(
            case,
            'version',
            self.reviewer,
            title='Revised text',
            counterparty='Fictional producer',
            contract_type='content',
            amount='2400000',
            currency='TZS',
            duration='six months',
            draft=file('v2.pdf', pdf_bytes('Amended exact text')),
        )
        second_version = services.latest_version(case)
        self.assertEqual(second_version.number, 2)
        self.assertEqual(second_version.checklists.get().answers, {})
        self._fill(case, second_version)
        second = self._freeze(
            case, condition_verification='Compared the redline and verified the rights schedules.'
        )
        self.assertEqual(second.version, second_version)
        self.assertEqual(second.package['condition_round'], 1)
        self._decision(case, second, expected=409, **self._package_data(first))
        self._decision(case, second, code=pyotp.TOTP(self.secret).at(self.now + timedelta(seconds=30)))
        self._action(case, 'reopen', self.signer)
        self._freeze(case, expected=400)
        self.assertEqual(case.reviews.count(), 2)
        self.assertEqual(Execution.objects.count(), 0)
        self.assertEqual(Release.objects.count(), 0)
        self.assertEqual(case.contract.versions.count(), 2)
        self.assertEqual(Decision.objects.get(review=second).outcome, 'clear')

    def test_stale_repeat_and_acting_out_of_sequence_do_not_advance_workflow(self):
        case = self._submit()
        revision = case.revision
        self._action(case, 'accept', self.triage, 403, verified='on')
        self._action(case, 'triage', self.triage)
        self._action(case, 'triage', self.triage, 409, revision=revision)
        self._action(case, 'accept', self.triage, 400)
        self.assertEqual(case.revision, revision + 1)
        self.assertEqual(case.events.count(), 2)
        self.assertEqual(case.matter.status, Status.PRELIMINARY)

    def test_duplicate_submission_is_idempotent_and_atomic(self):
        data = self._submission()
        first = self.staff.post(reverse('new_request'), data)
        self.assertEqual(first.status_code, 302)
        second = self.staff.post(reverse('new_request'), data)
        self.assertEqual(second.url, first.url)
        self.assertEqual(
            self.staff.post(reverse('new_request'), data | {'title': 'different'}).status_code, 409
        )
        self.assertEqual(Case.objects.count(), 1)
        self.assertEqual(Event.objects.filter(command='submit').count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='case_submit').count(), 1)

    def test_role_scope_current_assignment_and_named_clearance(self):
        case = self._submit(contract=True)
        self._action(case, 'triage', self.triage)
        self._action(case, 'accept', self.triage, verified='on')
        case.matter.classification = Classification.RESTRICTED
        case.matter.save(update_fields=['classification'])
        url = reverse('contract_detail', args=[case.pk])
        self.assertEqual(self.signer.get(url).status_code, 404)
        self.assertNotContains(self.signer.get(reverse('contract_register')), case.contract.reference)
        expire = timezone.now() + timedelta(days=2)
        for user in (self.senior, self.officer):
            MatterAccess.objects.create(
                matter=case.matter,
                user=user,
                classification=Classification.RESTRICTED,
                expires_at=expire,
                granted_by=self.head,
            )
        self._action(
            case,
            'assign',
            self.triage,
            400,
            officer=self.officer2.pk,
            due_on=self.day.isoformat(),
            priority='high',
        )
        self._action(
            case,
            'assign',
            self.triage,
            officer=self.officer.pk,
            due_on=self.day.isoformat(),
            priority='high',
        )
        self.assertEqual(self.reviewer.get(url).status_code, 200)
        self.assertContains(self.reviewer.get(reverse('inbox')), 'Legal work assigned to you')
        MatterAccess.objects.filter(user=self.officer).update(revoked_at=timezone.now())
        self.assertEqual(self.reviewer.get(url).status_code, 404)
        self.assertNotContains(self.reviewer.get(reverse('inbox')), 'Legal work assigned to you')
        self.assertEqual(authenticate_as(self.ict).get(reverse('contract_register')).status_code, 403)
        self.assertEqual(authenticate_as(self.other_senior).get(url).status_code, 404)

    def test_reassignment_preserves_history_and_revocation_hides_notice(self):
        case = self._submit()
        self._start(case)
        self._action(
            case,
            'assign',
            self.triage,
            officer=self.officer2.pk,
            due_on=self.day.isoformat(),
            priority='urgent',
        )
        self.assertEqual(
            LegalAssignment.objects.filter(matter_id=case.pk, revoked_at__isnull=False).get().officer,
            self.officer,
        )
        self.assertEqual(self.reviewer.get(reverse('matter_detail', args=[case.pk])).status_code, 404)
        self.assertNotContains(self.reviewer.get(reverse('inbox')), 'Legal work assigned to you')
        RoleGrant.objects.filter(user=self.officer2).update(expires_at=timezone.now())
        self.assertEqual(
            authenticate_as(self.officer2).get(reverse('matter_detail', args=[case.pk])).status_code, 403
        )
        self._action(
            case,
            'assign',
            self.triage,
            400,
            officer=self.officer2.pk,
            due_on=self.day.isoformat(),
            priority='high',
        )

    def test_head_cannot_approve_own_recommendation_or_self_intake(self):
        grant(self.officer, RoleCode.HEAD)
        case = self._submit()
        self._start(case)
        review = self._freeze(case)
        self.assertNotContains(
            self.reviewer.get(reverse('case_review', args=[case.pk, review.pk])), 'Record Head decision'
        )
        self._action(case, 'decide', self.reviewer, 403, **self._package_data(review), outcome='clear')
        self.assertFalse(case.reviews.filter(decision__isnull=False).exists())
        grant(self.head, RoleCode.STAFF)
        self.staff = authenticate_as(self.head)
        own = self._submit()
        self._action(own, 'triage', self.signer, 403)

    def test_real_step_up_rejects_sign_in_code_and_wrong_code_before_accepting_new_code(self):
        case = self._submit()
        self._start(case)
        review = self._freeze(case)
        now_step = pyotp.TOTP(self.secret).timecode(timezone.now())
        TotpDevice.objects.filter(user=self.head).update(last_step=now_step)
        self._decision(case, review, expected=400, code='not-totp', demo_confirmation='on')
        self._decision(case, review, expected=400, code=pyotp.TOTP(self.secret).at(now_step * 30))
        self.assertEqual(case.matter.status, Status.HEAD_REVIEW)
        self.assertFalse(Decision.objects.exists())
        self.assertEqual(AuditLog.objects.filter(action='case_step_up_failed').count(), 1)
        self._decision(case, review, code=pyotp.TOTP(self.secret).at((now_step + 1) * 30))
        self.assertEqual(Decision.objects.get().assurance, 'totp')
        self.assertEqual(Decision.objects.get().decided_by, self.head)

    def test_head_return_then_reject_publishes_only_reviewed_disposition(self):
        case = self._submit()
        self._start(case)
        first = self._freeze(case)
        self._decision(case, first, outcome='return')
        self.assertEqual(case.matter.status, Status.REVIEW)
        self.assertEqual(Decision.objects.get().outcome, 'return')
        second = self._freeze(case, recommendation='reject', response='')
        self._decision(
            case,
            second,
            outcome='reject',
            code=pyotp.TOTP(self.secret).at(self.now + timedelta(seconds=30)),
            public_message='This request cannot proceed. Please contact Legal Services.',
        )
        page = self.staff.get(reverse('staff_request', args=[case.pk]))
        self.assertContains(page, 'This request cannot proceed.')
        self.assertNotContains(page, 'DLS PRIVATE')
        self.assertEqual(case.matter.status, Status.REJECTED)
        self.assertEqual(case.reviews.count(), 2)

    def test_strategic_decision_requires_escalation_evidence_from_its_package(self):
        case = self._submit(contract=True)
        version = self._start(case)
        self._fill(case, version, risk='strategic')
        self._action(case, 'upload', self.reviewer, kind='internal', document=file('escalation.pdf'))
        record = case.documents.get(name='escalation.pdf')
        review = self._freeze(case)
        self._decision(case, review, expected=400)
        self._decision(case, review, expected=400, gate_evidence=uuid4())
        self._decision(case, review, gate_evidence=record.pk)
        self.assertEqual(Decision.objects.get().gate_evidence_id, record.pk)

    def test_pending_upload_blocks_intake_and_download_until_server_rescans(self):
        with self.settings(CLAMD_SOCKET='/not-a-configured-clamav.sock'):
            case = self._submit(contract=True)
        draft = case.documents.get()
        self.assertEqual(draft.scan, Document.Scan.PENDING)
        self.assertNotIn(b'%PDF', bytes(draft.blob.ciphertext))
        staff = reverse('staff_document', args=[case.pk, draft.pk])
        legal = reverse('legal_document', args=[case.pk, draft.pk])
        self.assertEqual(self.staff.get(staff).status_code, 404)
        self.assertEqual(self.signer.get(legal).status_code, 404)
        self._action(case, 'triage', self.triage)
        self._action(case, 'accept', self.triage, 400, verified='on')
        with self.settings(CLAMD_SOCKET='/not-a-configured-clamav.sock'):
            self._action(case, 'rescan', self.triage, 400, pending=draft.pk)
        self._action(case, 'rescan', self.triage, pending=draft.pk)
        self.assertEqual(Document.objects.get(pk=draft.pk).scan, Document.Scan.CLEAN)
        self.assertEqual(self.staff.get(staff).status_code, 200)
        self._action(case, 'accept', self.triage, verified='on')
        self.assertEqual(Contract.objects.count(), 1)
        with self.settings(DOCUMENT_KEY=Fernet.generate_key().decode()):
            self.assertEqual(self.signer.get(legal).status_code, 503)

    def test_cross_case_evidence_and_documents_cannot_be_used(self):
        case = self._submit(contract=True)
        version = self._start(case)
        unrelated = self._submit(attachment=file('unrelated.pdf'))
        foreign = unrelated.documents.get()
        self.assertEqual(
            self.signer.get(reverse('legal_document', args=[case.pk, foreign.pk])).status_code, 404
        )
        self.assertEqual(
            self.staff.get(reverse('staff_document', args=[case.pk, foreign.pk])).status_code, 404
        )
        response = self._part(
            case, version, 'I', {'I-01_choice': 'yes', 'I-01_evidence': foreign.pk}, expected=400
        )
        self.assertContains(response, 'Select a valid choice', status_code=400)
        self.assertEqual(version.checklists.get().answers, {})

    def test_invalid_uploads_and_csrf_cannot_create_requests(self):
        url = reverse('new_request')
        secure = authenticate_as(self.employee, client=Client(enforce_csrf_checks=True))
        self.assertEqual(secure.post(url, self._submission()).status_code, 403)
        for bad in (
            file('malformed.pdf', b'%PDF-1.5\nnot a document\n%%EOF'),
            file('archive.exe'),
            file('too-big.pdf', b' ' * (5 * 1024 * 1024 + 1)),
        ):
            self.assertEqual(self.staff.post(url, self._submission(attachment=bad)).status_code, 400)
        over = self.staff.generic(
            'POST', url, b'x', content_type='multipart/form-data; boundary=x', CONTENT_LENGTH='23000000'
        )
        self.assertEqual(over.status_code, 413)
        zipped = BytesIO()
        with ZipFile(zipped, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('../ignored.xml', '<data/>')
            archive.writestr('[Content_Types].xml', '<Types/>')
        self.assertEqual(
            self.staff.post(
                url, self._submission(attachment=file('malformed.docx', zipped.getvalue()))
            ).status_code,
            400,
        )
        self.assertFalse(Case.objects.exists())
        self.assertFalse(Document.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action='case_submit').exists())
