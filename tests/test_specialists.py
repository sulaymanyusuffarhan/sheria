"""Specialist HTTP flows, shared revisions, approval isolation and linked evidence."""

from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import pyotp
from cryptography.fernet import Fernet
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader

from casework import checklist
from casework import services as workflow
from casework.models import Case, Decision, Execution
from identity.models import RoleCode, TotpDevice
from library.models import Item
from matters.models import (
    Classification,
    LegalAssignment,
    LegalMatter,
    LegalRequest,
    MatterAccess,
    Status,
)
from specialists.models import Assessment, Hearing, Licence, OpinionIssue

from .factories import authenticate_as, division, employee, grant
from .test_casework import DOCUMENT_KEY, MFA_KEY, file
from .test_casework_transactions import request_for


@override_settings(
    ENV='test', DOCUMENT_KEY=DOCUMENT_KEY, MFA_KEY=MFA_KEY, LOCAL_AUTH_ENABLED=True, CLAMD_SOCKET=''
)
class SpecialistFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.unit, cls.other_unit = division('MEDIA'), division('CORP')
        cls.senior = employee('senior', cls.unit, roles=(RoleCode.SENIOR,))
        cls.head = employee('head', cls.unit, roles=(RoleCode.HEAD,))
        cls.author = employee('officer', cls.unit, roles=(RoleCode.OFFICER,))
        cls.other_officer = employee('officer2', cls.other_unit, roles=(RoleCode.OFFICER,))
        cls.outsider = employee('other-senior', cls.other_unit, roles=(RoleCode.SENIOR,))
        cls.staff = employee('requester', cls.unit)
        cls.hod = employee('hod', cls.unit, roles=(RoleCode.HOD, RoleCode.STAFF))
        cls.records = employee('records', cls.unit, roles=(RoleCode.RECORDS,))
        cls.manager = employee('reporting', cls.unit, roles=(RoleCode.MANAGEMENT,))
        cls.ict = employee('ict', roles=(RoleCode.ICT,))
        cls.secret = pyotp.random_base32()
        TotpDevice.objects.create(
            user=cls.head, encrypted_secret=Fernet(MFA_KEY.encode()).encrypt(cls.secret.encode())
        )

    def setUp(self):
        self.today = timezone.localdate()
        self.due = self.today + timedelta(days=5)
        self.now = timezone.now()
        clock = patch('identity.security.timezone', wraps=timezone)
        clock.start().now.side_effect = lambda: self.now
        self.addCleanup(clock.stop)
        self.sen = authenticate_as(self.senior)
        self.review = authenticate_as(self.author)
        self.lead = authenticate_as(self.head)
        self.portal = authenticate_as(self.staff)

    def opened(self, category='litigation', *, prefix=None, **attrs):
        module = prefix or {'ip': 'rights', 'opinion': 'opinions'}.get(category, category)
        data = {
            'submission_key': uuid4(),
            'title': f'Matter: {module}',
            'summary': 'Private background',
            'category': category,
            'department': self.unit.pk,
            'required_on': self.due,
            'priority': 'normal',
            'classification': Classification.INTERNAL,
        } | attrs
        result = self.sen.post(reverse('specialist_create', args=[module]), data)
        self.assertEqual(result.status_code, 302, getattr(result.context, 'dicts', None))
        return Case.objects.get(submission_key=data['submission_key'])

    def post(self, client, endpoint, case, *, want=302, **fields):
        case.refresh_from_db()
        result = client.post(endpoint, {'revision': case.revision, **fields})
        self.assertEqual(result.status_code, want, getattr(result.context, 'dicts', None))
        if want != 302:
            before = case.revision
            case.refresh_from_db()
            self.assertEqual(case.revision, before)
        case.refresh_from_db()
        return result

    def sp(self, case, module, command, client=None, *, want=302, **fields):
        return self.post(
            client or self.sen,
            reverse('specialist_action', args=[module, case.pk, command]),
            case,
            want=want,
            **fields,
        )

    def cw(self, case, command, client=None, *, want=302, **fields):
        return self.post(
            client or self.sen,
            reverse('case_action', args=[case.pk, command]),
            case,
            want=want,
            **({'reason': 'Synthetic internal rationale'} | fields),
        )

    def start(self, case):
        self.cw(case, 'assign', officer=self.author.pk, due_on=self.due, priority='normal')
        self.cw(case, 'begin', self.review)
        return case

    def docket(self, **attrs):
        case = self.start(self.opened(**attrs))
        self.sp(
            case,
            'litigation',
            'register',
            court='Test Court Registry',
            external_number='LR-26-TEST',
            filed_on=self.today,
            case_type='Synthetic civil case',
            advocate='Test advocate',
        )
        return case

    def repository_item(
        self, case, *, sealed=False, name='Versioned proof', filename='evidence-v1.pdf', **params
    ):
        response = self.post(
            self.review,
            reverse('library_create', args=[case.pk]),
            case,
            title=name,
            tags='replay-tag',
            category='court',
            document=file(filename),
            provenance='Fictional original',
            sealed='on' if sealed else '',
            **params,
        )
        return Item.objects.get(pk=response.url.rstrip('/').split('/')[-1])

    def step_up(self):
        return pyotp.TOTP(self.secret).at(self.now)

    def permit(self, case, *users):
        for user in users:
            MatterAccess.objects.create(
                matter_id=case.pk,
                user=user,
                classification=case.matter.classification,
                expires_at=self.now + timedelta(days=1),
                granted_by=self.head,
            )


class SpecialistTests(SpecialistFixture):
    def test_internal_registration_classification_scope_idempotency_and_csrf(self):
        key = uuid4()
        case = self.opened(
            submission_key=key, title='Private protected', classification=Classification.RESTRICTED
        )
        self.assertEqual(
            self.opened(
                submission_key=key, title='Private protected', classification=Classification.RESTRICTED
            ).pk,
            case.pk,
        )
        self.assertFalse(LegalRequest.objects.filter(pk=case.pk).exists())
        self.assertEqual(case.events.get().command, 'open_internal')
        url = reverse('specialist_detail', args=['litigation', case.pk])
        self.assertEqual(self.sen.get(url).status_code, 404)
        self.assertEqual(self.lead.get(url).status_code, 404)
        self.assertNotContains(
            self.sen.get(reverse('specialist_index', args=['litigation']), {'q': 'Private protected'}),
            'Private protected</a>',
        )
        self.permit(case, self.senior)
        self.assertContains(self.sen.get(url), 'Private protected')
        self.assertEqual(self.portal.get(reverse('staff_request', args=[case.pk])).status_code, 404)
        self.assertEqual(authenticate_as(self.outsider).get(url).status_code, 404)
        self.assertEqual(
            self.review.get(reverse('specialist_create', args=['litigation'])).status_code, 403
        )
        self.assertEqual(
            self.sen.get(reverse('specialist_detail', args=['rights', case.pk])).status_code, 404
        )
        self.assertEqual(
            self.portal.get(reverse('specialist_index', args=['litigation'])).status_code, 403
        )
        csrf = authenticate_as(self.senior, client=Client(enforce_csrf_checks=True))
        self.assertEqual(
            csrf.post(reverse('specialist_create', args=['litigation']), {}).status_code, 403
        )
        version = self.sen.post(
            reverse('specialist_create', args=['litigation']),
            {
                'submission_key': key,
                'title': 'Changed',
                'category': 'litigation',
                'department': self.unit.pk,
                'required_on': self.due,
                'priority': 'normal',
                'summary': 'Private background',
                'classification': 'restricted',
            },
        )
        self.assertEqual(version.status_code, 409)

    def test_litigation_calendar_and_disposition(self):
        case = self.docket()
        record = case.litigation
        detail = reverse('specialist_detail', args=['litigation', case.pk])
        calendar = reverse('court_calendar')
        self.sp(case, 'litigation', 'lit_party', self.review, name='Fictional claimant', role='claimant')
        self.sp(
            case,
            'litigation',
            'lit_party',
            self.review,
            want=400,
            name='fictional claimant',
            role='claimant',
        )
        first = self.today + timedelta(days=3)
        due = first + timedelta(days=1)
        self.sp(
            case,
            'litigation',
            'lit_hearing',
            self.review,
            starts_at=f'{first.isoformat()}T09:15',
            room='Room A',
            purpose='Directions only',
        )
        hearing = record.hearings.get()
        self.assertEqual(timezone.localtime(hearing.starts_at).hour, 9)
        stale = case.revision
        self.sp(
            case,
            'litigation',
            'lit_reschedule',
            self.review,
            hearing=hearing.pk,
            starts_at=f'{due.isoformat()}T10:30',
            room='Room B',
            purpose='Adjourned agenda',
            reason='Verified court change',
        )
        hearing.refresh_from_db()
        self.assertEqual(hearing.status, Hearing.Status.ADJOURNED)
        self.assertTrue(hearing.milestone.is_cancelled)
        change = hearing.replacement
        self.assertEqual(change.milestone.date, due)
        resched = reverse('specialist_action', args=['litigation', case.pk, 'lit_reschedule'])
        self.assertEqual(
            self.review.post(resched, {'revision': stale, 'hearing': hearing.pk}).status_code, 409
        )
        self.assertContains(
            self.review.get(calendar, {'month': due.strftime('%Y-%m'), 'day': due}), 'Adjourned agenda'
        )
        self.assertNotContains(
            self.review.get(calendar, {'month': first.strftime('%Y-%m')}), 'Directions only'
        )
        self.assertContains(
            self.review.get(calendar, {'month': first.strftime('%Y-%m'), 'history': '1'}),
            'Directions only',
        )
        self.assertNotContains(
            authenticate_as(self.outsider).get(calendar, {'month': due.strftime('%Y-%m')}),
            'Adjourned agenda',
        )
        self.assertEqual(self.portal.get(calendar).status_code, 403)
        self.assertEqual(self.lead.get(calendar, {'month': '2026-17'}).status_code, 404)
        self.sp(
            case,
            'litigation',
            'lit_hearing_result',
            self.review,
            want=400,
            hearing=change.pk,
            hearing_status='held',
            reason='Not yet',
        )
        item = self.repository_item(case)
        doc = item.versions.get().document
        self.sp(
            case,
            'litigation',
            'lit_progress',
            self.review,
            kind='order',
            occurred_on=self.today,
            note='Fictional order',
            evidence=doc.pk,
        )
        self.sp(
            case,
            'litigation',
            'lit_progress',
            self.review,
            kind='strategy',
            occurred_on=self.today,
            note='SECRET STRATEGY MARKER',
        )
        self.assertContains(self.review.get(detail), 'SECRET STRATEGY MARKER')
        self.assertNotContains(self.sen.get(detail), 'SECRET STRATEGY MARKER')
        self.assertContains(
            self.sen.get(detail), reverse('library_download', args=[item.pk, item.versions.get().pk])
        )
        self.sp(
            case,
            'litigation',
            'lit_cost',
            self.review,
            occurred_on=self.today,
            description='Test filing fee',
            amount='105.20',
            currency='tzs',
        )
        self.assertEqual(record.costs.get().currency, 'TZS')
        result = {
            'kind': 'judgment',
            'occurred_on': self.today,
            'reason': 'Verified synthetic judgment',
            'evidence': doc.pk,
            'externally_verified': 'on',
        }
        self.sp(case, 'litigation', 'lit_outcome', self.lead, want=400, **result)
        self.sp(
            case,
            'litigation',
            'lit_hearing_result',
            self.review,
            hearing=change.pk,
            hearing_status='cancelled',
            reason='Court cancelled',
        )
        self.sp(case, 'litigation', 'lit_outcome', self.sen, **result)
        self.assertEqual(case.matter.status, Status.COMPLETED)
        self.cw(case, 'close', want=403, retention_confirmed='on')
        self.sp(
            case, 'litigation', 'lit_close', confirmed='on', reason='Appeal window and custody reviewed'
        )
        self.assertEqual(case.matter.status, Status.CLOSED)
        self.sp(case, 'litigation', 'lit_party', want=403, name='Another party', role='other')
        self.assertNotContains(
            self.review.get(calendar, {'month': due.strftime('%Y-%m')}), 'Adjourned agenda'
        )

    def test_litigation_settlement_is_documentary_and_requires_fresh_head(self):
        case = self.docket()
        self.cw(case, 'upload', self.review, kind='internal', document=file('settlement.pdf'))
        proof = case.documents.get(name='settlement.pdf')
        self.cw(case, 'upload', self.review, kind='internal', document=file('authority.pdf'))
        authority = case.documents.get(name='authority.pdf')
        data = {
            'kind': 'settlement',
            'occurred_on': self.today,
            'reason': 'Externally authorised fictional settlement',
            'evidence': proof.pk,
            'authority': authority.pk,
            'externally_verified': 'on',
        }
        self.sp(case, 'litigation', 'lit_outcome', self.sen, want=400, **data)
        self.sp(
            case,
            'litigation',
            'lit_outcome',
            self.lead,
            want=400,
            **data,
            code=f'{(int(self.step_up()) + 1) % 1_000_000:06d}',
        )
        self.sp(
            case,
            'litigation',
            'lit_outcome',
            self.lead,
            want=400,
            **(data | {'authority': proof.pk, 'code': self.step_up()}),
        )
        self.sp(case, 'litigation', 'lit_outcome', self.lead, **data, code=self.step_up())
        self.assertEqual(case.litigation.outcome.assurance, 'totp')
        self.assertEqual(case.litigation.outcome.authority, authority)

    def test_opinion_requires_exact_independent_reviews_and_explicit_issue(self):
        submit = self.portal.post(
            reverse('new_request'),
            {
                'submission_key': uuid4(),
                'title': 'Staff opinion example',
                'category': 'opinion',
                'summary': 'Authorised intake facts',
                'office': 'Programming',
                'contact': 'employee@example.invalid',
                'required_on': self.due,
                'priority': 'normal',
                'declaration': 'on',
            },
        )
        self.assertEqual(submit.status_code, 302)
        case = Case.objects.get(pk=submit.url.rstrip('/').split('/')[-1])
        staff_url = reverse('staff_request', args=[case.pk])
        self.cw(case, 'triage')
        self.cw(case, 'accept', verified='on')
        self.start(case)
        self.sp(case, 'opinions', 'register')
        payload = {
            'question': 'Synthetic opinion question',
            'facts': 'Verified facts',
            'authorities': 'Test citations',
            'analysis': 'EARLIER DRAFT MARKER',
            'conclusion': 'Conclusion for approval',
        }
        freeze = {
            'recommendation': 'approve',
            'response': 'Approved staff summary only.',
            'share_opinion': 'on',
            'confirmed': 'on',
        }
        self.cw(case, 'submit_review', self.review, want=400, **freeze)
        self.sp(case, 'opinions', 'opinion_version', self.review, **payload)
        first = case.opinion.versions.get()
        self.sp(
            case,
            'opinions',
            'opinion_senior',
            self.review,
            want=403,
            version_id=first.pk,
            verified='on',
            senior_outcome='endorse',
            reason='Cannot self-review',
        )
        self.sp(
            case,
            'opinions',
            'opinion_senior',
            version_id=first.pk,
            verified='on',
            senior_outcome='return',
            reason='Improve facts',
        )
        self.cw(case, 'submit_review', self.review, want=400, **freeze)
        self.sp(
            case,
            'opinions',
            'opinion_version',
            self.review,
            **(payload | {'analysis': 'APPROVED OPINION MARKER'}),
        )
        latest = case.opinion.versions.first()
        self.sp(
            case,
            'opinions',
            'opinion_senior',
            want=409,
            version_id=first.pk,
            verified='on',
            senior_outcome='endorse',
            reason='Stale draft',
        )
        self.sp(
            case,
            'opinions',
            'opinion_senior',
            version_id=latest.pk,
            verified='on',
            senior_outcome='endorse',
            reason='Verified citations for v2',
        )
        self.cw(case, 'submit_review', self.review, **freeze)
        frozen = case.reviews.get()
        self.assertEqual(frozen.package['opinion']['version_id'], str(latest.pk))
        self.assertContains(
            self.lead.get(reverse('case_review', args=[case.pk, frozen.pk])), 'APPROVED OPINION MARKER'
        )
        self.assertNotContains(self.portal.get(staff_url), 'APPROVED OPINION MARKER')
        self.assertEqual(
            self.lead.get(reverse('opinion_pdf', args=[case.pk, frozen.pk])).status_code, 404
        )
        self.sp(case, 'opinions', 'opinion_version', self.review, want=403, **payload)
        approval = {
            'review_id': frozen.pk,
            'package_digest': frozen.digest,
            'outcome': 'clear',
            'gates_verified': 'on',
            'no_conflict': 'on',
            'code': self.step_up(),
        }
        grant(self.senior, RoleCode.HEAD)
        self.cw(case, 'decide', self.sen, want=403, **approval)
        self.cw(case, 'decide', self.lead, **approval)
        self.assertEqual(frozen.decision.outcome, Decision.Outcome.CLEAR)
        self.assertFalse(OpinionIssue.objects.exists())
        self.cw(
            case,
            'issue',
            self.lead,
            want=409,
            review_id=frozen.pk,
            package_digest='f' * 64,
            confirmed='on',
        )
        self.cw(
            case, 'issue', self.lead, review_id=frozen.pk, package_digest=frozen.digest, confirmed='on'
        )
        self.assertEqual(OpinionIssue.objects.get().staff_recipient, self.staff)
        self.assertContains(self.portal.get(staff_url), 'APPROVED OPINION MARKER')
        self.assertNotContains(self.portal.get(staff_url), 'EARLIER DRAFT MARKER')
        # Give the HoD the separate staff-safe projection; not access to the opinion itself.
        LegalRequest.objects.filter(pk=case.pk).update(department_visible=True)
        self.assertNotContains(
            authenticate_as(self.hod).get(staff_url + '?scope=department'), 'APPROVED OPINION MARKER'
        )
        url = reverse('staff_opinion_pdf', args=[case.pk, frozen.pk])
        pdf = self.portal.get(url)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers['Cache-Control'], 'no-store, private')
        text = ' '.join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
        self.assertIn('APPROVED OPINION MARKER', text)
        self.assertNotIn('Verified citations for v2', text)
        LegalMatter.objects.filter(pk=case.pk).update(classification=Classification.RESTRICTED)
        self.assertEqual(self.portal.get(url).status_code, 404)
        self.assertNotContains(self.portal.get(staff_url), 'APPROVED OPINION MARKER')

    def test_rights_renewal_incident_scope_and_deadlines(self):
        docket = self.docket(title='Independent case reference')
        case = self.start(self.opened('ip'))
        self.sp(case, 'rights', 'register', description='Fictional acquisition portfolio')
        version = self.repository_item(case, name='Licence source').versions.get()
        foreign = self.repository_item(docket, name='Other matter source').versions.get()
        sealed = self.repository_item(case, sealed=True, name='Sealed source').versions.get()
        terms = {
            'kind': 'archive',
            'asset': 'Fictional news image',
            'licensor': 'Fictional artist',
            'platform': 'OTT',
            'territory': 'TZ',
            'terms': 'One credited use for training',
            'starts_on': self.today,
            'ends_on': self.due,
            'evidence': version.document_id,
            'provenance_verified': 'on',
        }
        self.sp(
            case,
            'rights',
            'rights_licence',
            self.review,
            want=400,
            **(terms | {'evidence': foreign.document_id}),
        )
        self.sp(
            case,
            'rights',
            'rights_licence',
            self.review,
            want=400,
            **(terms | {'evidence': sealed.document_id}),
        )
        self.sp(case, 'rights', 'rights_licence', self.review, **terms)
        initial = Licence.objects.get()
        self.sp(case, 'rights', 'rights_renew', self.review, want=400, licence=initial.pk, **terms)
        self.sp(
            case,
            'rights',
            'rights_renew',
            self.review,
            licence=initial.pk,
            **(
                terms
                | {
                    'starts_on': self.due + timedelta(days=1),
                    'ends_on': self.due + timedelta(days=30),
                    'terms': 'One renewal; different term',
                }
            ),
        )
        initial.refresh_from_db()
        self.assertEqual(initial.renewal.prior, initial)
        self.sp(
            case,
            'rights',
            'rights_incident',
            self.review,
            licence=initial.pk,
            occurred_on=self.today,
            allegation='Potential replay',
            immediate_action='Preserve usage log',
            referred_matter=docket.pk,
            evidence=version.document_id,
        )
        incident = case.rightsportfolio.incidents.get()
        detail = reverse('specialist_detail', args=['rights', case.pk])
        self.assertContains(self.lead.get(detail), docket.matter.reference)
        LegalMatter.objects.filter(pk=docket.pk).update(classification=Classification.RESTRICTED)
        self.assertNotContains(self.lead.get(detail), docket.matter.reference)
        self.sp(
            case,
            'rights',
            'rights_resolve',
            self.review,
            want=403,
            incident=incident.pk,
            reason='Not authorised',
        )
        self.sp(case, 'rights', 'rights_resolve', incident=incident.pk, reason='Documented resolution')
        self.assertContains(self.review.get(reverse('legal_deadlines')), 'Rights expiry')
        self.assertNotContains(
            authenticate_as(self.outsider).get(reverse('legal_deadlines')), case.matter.reference
        )
        LegalAssignment.objects.filter(matter_id=case.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.review.get(detail).status_code, 404)

    def test_rights_link_only_to_an_executed_contract_in_live_scope(self):
        intake = self.portal.post(
            reverse('new_request'),
            {
                'submission_key': uuid4(),
                'title': 'Fictional content agreement',
                'category': 'contract',
                'summary': 'Synthetic sourcing workflow.',
                'office': 'Programming',
                'contact': 'test@example.invalid',
                'required_on': self.due,
                'priority': 'normal',
                'declaration': 'on',
                'counterparty': 'Fictional Media',
                'contract_type': 'content',
                'amount': '2000.00',
                'currency': 'TZS',
                'duration': 'Two months',
                'draft': file(),
            },
        )
        self.assertEqual(intake.status_code, 302)
        contract = Case.objects.get(pk=intake.url.rstrip('/').split('/')[-1])
        self.cw(contract, 'triage')
        self.cw(contract, 'accept', verified='on')
        self.start(contract)
        version = workflow.latest_version(contract)
        actor = request_for(self.author)
        for section in checklist.REVIEW_SECTIONS:
            contract.refresh_from_db()
            workflow.save_checklist(
                actor,
                contract.pk,
                version.pk,
                section.code,
                {
                    'revision': contract.revision,
                    'answers': {
                        key: {'choice': 'yes', 'remarks': 'Synthetic check', 'evidence': []}
                        for key, _ in section.items
                    },
                },
            )
        contract.refresh_from_db()
        workflow.save_checklist(
            actor,
            contract.pk,
            version.pk,
            'XI',
            {
                'revision': contract.revision,
                'risk_class': 'low',
                'risks': [
                    {
                        'category': 'Scope',
                        'description': 'Synthetic reuse restriction',
                        'likelihood': 'low',
                        'impact': 'low',
                        'mitigation': 'Keep to test scope',
                        'responsible': 'Test officer',
                    }
                ],
            },
        )
        self.cw(
            contract,
            'submit_review',
            self.review,
            recommendation='approve',
            response='Clearance for this test.',
            confirmed='on',
        )
        frozen = contract.reviews.get()
        pin = {'review_id': frozen.pk, 'package_digest': frozen.digest}
        self.cw(
            contract,
            'decide',
            self.lead,
            **pin,
            outcome='clear',
            gates_verified='on',
            no_conflict='on',
            code=self.step_up(),
        )
        rights = self.start(self.opened('ip'))
        self.sp(rights, 'rights', 'register', description='Verified test licence')
        url = reverse('specialist_action', args=['rights', rights.pk, 'rights_licence'])
        self.assertNotContains(self.review.get(url), version.contract.reference)
        self.now += timedelta(seconds=31)
        self.cw(
            contract,
            'execute',
            self.lead,
            **pin,
            signed_on=self.today,
            effective_on=self.today,
            expires_on=self.due,
            signatories='External authority in test fixture',
            prerequisites='Synthetic execution verification',
            gates_verified='on',
            text_matches='on',
            code=self.step_up(),
            document=file('signed-synthetic.pdf'),
        )
        execution = Execution.objects.get(version=version)
        self.assertContains(self.review.get(url), version.contract.reference)
        proof = self.repository_item(rights).versions.get().document
        self.sp(
            rights,
            'rights',
            'rights_licence',
            self.review,
            kind='programme',
            asset='Fictional licensed clip',
            licensor='Fictional Media',
            platform='Terrestrial',
            territory='TZ',
            terms='Single training use',
            starts_on=self.today,
            ends_on=self.due,
            evidence=proof.pk,
            execution=execution.pk,
            provenance_verified='on',
        )
        detail = reverse('specialist_detail', args=['rights', rights.pk])
        self.assertContains(self.review.get(detail), version.contract.reference)
        LegalMatter.objects.filter(pk=contract.pk).update(classification=Classification.CONFIDENTIAL)
        self.assertNotContains(self.review.get(detail), version.contract.reference)
        self.assertNotContains(self.review.get(url), version.contract.reference)

    def test_compliance_source_and_corrective_evidence_gates(self):
        case = self.start(self.opened('compliance'))
        self.sp(
            case,
            'compliance',
            'register',
            requirement='Check fictional control',
            control_area='Retention trial',
            responsible=self.staff.pk,
            due_on=self.today,
        )
        source = {
            'title': 'Synthetic policy, no legal effect',
            'citation': 'TRAINING/1',
            'jurisdiction': 'Fictional',
            'url': 'https://example.invalid/source',
            'effective_on': self.today,
        }
        self.sp(
            case,
            'compliance',
            'cmp_source',
            self.review,
            want=400,
            **(source | {'url': 'javascript:alert(1)'}),
        )
        self.sp(case, 'compliance', 'cmp_source', self.sen, **source)
        latest = case.obligation.sources.get()
        assess = {
            'source_id': latest.pk,
            'result': 'gap',
            'findings': 'Evidence not available',
            'next_review': self.due + timedelta(days=6),
            'due_on': self.due,
            'plan': 'Reconcile the log',
            'responsible': self.staff.pk,
        }
        self.sp(case, 'compliance', 'cmp_assess', self.review, want=403, **assess)
        self.sp(
            case,
            'compliance',
            'cmp_verify',
            self.sen,
            want=403,
            source_id=latest.pk,
            verified='on',
            reason='Self verification',
        )
        self.sp(
            case,
            'compliance',
            'cmp_verify',
            self.lead,
            source_id=latest.pk,
            verified='on',
            reason='Verified fictitious scope',
        )
        self.sp(case, 'compliance', 'cmp_assess', self.review, want=400, **(assess | {'plan': ''}))
        self.assertFalse(Assessment.objects.exists())
        self.sp(case, 'compliance', 'cmp_assess', self.review, **assess)
        gap = case.obligation.assessments.get()
        action = gap.corrective_action
        doc = self.repository_item(case).versions.get().document
        self.sp(
            case,
            'compliance',
            'cmp_assess',
            self.review,
            want=400,
            **(assess | {'result': 'demonstrated', 'evidence': doc.pk}),
        )
        self.sp(
            case,
            'compliance',
            'cmp_remediate',
            self.sen,
            action=action.pk,
            summary='First reconciliation',
            evidence=doc.pk,
        )
        sub = action.remediations.get()
        self.sp(
            case,
            'compliance',
            'cmp_verify_action',
            self.sen,
            want=400,
            remediation=sub.pk,
            verdict='verify',
            reason='Self verification',
        )
        self.sp(
            case,
            'compliance',
            'cmp_verify_action',
            self.lead,
            remediation=sub.pk,
            verdict='return',
            reason='Need second confirmation',
        )
        self.sp(
            case,
            'compliance',
            'cmp_remediate',
            self.review,
            action=action.pk,
            summary='Second reconciliation',
            evidence=doc.pk,
        )
        self.sp(
            case,
            'compliance',
            'cmp_verify_action',
            self.sen,
            remediation=action.remediations.first().pk,
            verdict='verify',
            reason='Reconciled',
        )
        self.assertTrue(action.milestone.is_cancelled)
        self.sp(
            case,
            'compliance',
            'cmp_assess',
            self.review,
            **(assess | {'result': 'demonstrated', 'evidence': doc.pk}),
        )
        final_assessment = case.obligation.assessments.first()
        self.sp(
            case,
            'compliance',
            'cmp_source',
            self.review,
            **(source | {'title': 'Revised synthetic policy'}),
        )
        view = self.sen.get(reverse('specialist_detail', args=['compliance', case.pk]))
        self.assertContains(view, 'Not assessed against the latest source')
        self.assertContains(view, 'Revised synthetic policy')
        self.assertTrue(final_assessment.milestone.is_cancelled)
        self.assertEqual(case.obligation.assessments.count(), 2)
