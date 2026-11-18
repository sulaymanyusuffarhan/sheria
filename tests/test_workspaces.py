"""Server-rendered and API role/scope matrix, data integrity and deliberate empty states. Revisions: Git."""

from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from identity.models import AccessRequest, Role, RoleCode, User
from identity.policy import ROLE_SPECS, Policy
from matters.models import (
    Category,
    Classification,
    LegalAssignment,
    LegalMatter,
    LegalRequest,
    MatterAccess,
    Milestone,
    RequestUpdate,
    StaffStatus,
    Status,
)

from .factories import authenticate_as, division, employee, grant


class WorkspaceScopesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.unit = division('RADIO')
        cls.other = division('FINANCE')
        cls.foreign = division('FIXTURE', is_demo=True)
        cls.now = timezone.now()
        cls.today = timezone.localdate()
        cls.sam = employee('sam', cls.unit)
        cls.other_staff = employee('peer', cls.unit)
        cls.staffer = employee('finance', cls.other)
        cls.head = employee('head', cls.unit, roles=(RoleCode.HEAD,))
        cls.officer = employee('officer', cls.other, roles=(RoleCode.OFFICER,))
        cls.senior = employee('senior', cls.unit, roles=(RoleCode.SENIOR,))
        cls.hod = employee('hod', cls.unit, roles=(RoleCode.HOD, RoleCode.STAFF))
        cls.records = employee('records', cls.unit, roles=(RoleCode.RECORDS,))
        cls.manager = employee('manager', roles=(RoleCode.MANAGEMENT,))
        cls.ict = employee('ict', roles=(RoleCode.ICT,))
        cls.audit = employee('auditor', roles=(RoleCode.AUDITOR,))
        cls.ordinary_location = cls.make_record(
            'RAD-01',
            cls.unit,
            requester=cls.sam,
            department_visible=True,
            internal='Confined private summary',
            title='Privately worded contract',
            status=Status.HEAD_REVIEW,
        )
        cls.ordinary_peer = cls.make_record(
            'RAD-02',
            cls.unit,
            requester=cls.other_staff,
            department_visible=False,
            status=Status.COMPLETED,
        )
        cls.other_unit = cls.make_record(
            'FIN-01', cls.other, requester=cls.staffer, department_visible=True, status=Status.REVIEW
        )
        cls.restricted = cls.make_record(
            'RAD-03',
            cls.unit,
            requester=cls.other_staff,
            department_visible=True,
            classification=Classification.RESTRICTED,
        )
        cls.named = cls.make_record(
            'RAD-04',
            cls.unit,
            requester=cls.sam,
            department_visible=True,
            classification=Classification.CONFIDENTIAL,
        )
        cls.demo = cls.make_record('SAMPLE-01', cls.foreign, requester=None, is_demo=True)
        cls.assignment = LegalAssignment.objects.create(
            matter=cls.ordinary_location,
            officer=cls.officer,
            starts_at=cls.now - timedelta(days=1),
            due_on=cls.today - timedelta(days=1),
        )
        LegalAssignment.objects.create(
            matter=cls.named,
            officer=cls.officer,
            starts_at=cls.now - timedelta(days=1),
            due_on=cls.today + timedelta(days=2),
        )
        LegalAssignment.objects.create(
            matter=cls.other_unit,
            officer=cls.officer,
            starts_at=cls.now + timedelta(days=1),
            due_on=cls.today + timedelta(days=5),
        )
        LegalAssignment.objects.create(
            matter=cls.restricted,
            officer=cls.officer,
            starts_at=cls.now - timedelta(days=4),
            ends_at=cls.now - timedelta(days=1),
            due_on=cls.today,
        )
        cls.clearance = MatterAccess.objects.create(
            user=cls.officer,
            matter=cls.named,
            granted_by=cls.head,
            classification=Classification.CONFIDENTIAL,
            expires_at=cls.now + timedelta(days=1),
        )
        RequestUpdate.objects.create(
            request=cls.ordinary_location.staff_request,
            status=StaffStatus.AWAITING,
            message='Published test timeline.',
            published_at=cls.now - timedelta(days=1),
        )
        RequestUpdate.objects.create(
            request=cls.ordinary_location.staff_request,
            status=StaffStatus.PROCESSING,
            message='Future update marker',
            published_at=cls.now + timedelta(days=1),
        )
        Milestone.objects.create(
            matter=cls.ordinary_location, kind=Milestone.Kind.EXPIRY, date=cls.today + timedelta(days=1)
        )
        Milestone.objects.create(
            matter=cls.restricted, kind=Milestone.Kind.EXPIRY, date=cls.today + timedelta(days=2)
        )
        Milestone.objects.create(
            matter=cls.ordinary_location,
            kind=Milestone.Kind.EXPIRY,
            date=cls.today + timedelta(days=1),
            is_cancelled=True,
        )

    @classmethod
    def make_record(
        cls,
        ref,
        department,
        *,
        requester=None,
        department_visible=False,
        title=None,
        internal='Fixture-only legal material',
        **fields,
    ):
        legal = LegalMatter.objects.create(
            reference=ref,
            title=title or f'Private {ref}',
            internal_summary=internal,
            department=department,
            category_id='contract',
            opened_at=cls.now - timedelta(days=2),
            **fields,
        )
        if requester:
            LegalRequest.objects.create(
                matter=legal,
                requester=requester,
                title=f'Published {ref}',
                summary='Staff-safe test context.',
                submitted_at=legal.opened_at,
                due_on=cls.today + timedelta(days=2),
                department_visible=department_visible,
                public_status=StaffStatus.AWAITING,
            )
        return legal

    def test_nine_roles_have_expected_workspace_routes_and_no_overreach(self):
        roles = [
            (self.sam, '/portal/', '/admin/matters/'),
            (self.hod, '/portal/', '/admin/matters/'),
            (self.officer, '/admin/', '/admin/accounts/'),
            (self.senior, '/admin/', '/admin/accounts/'),
            (self.head, '/admin/', '/admin/accounts/'),
            (self.ict, '/admin/ict/', '/admin/matters/'),
            (self.manager, '/admin/management/', '/admin/matters/'),
            (self.audit, '/admin/audit/', '/admin/matters/'),
            (self.records, '/admin/records/', '/admin/matters/'),
        ]
        for user, landing, denied in roles:
            with self.subTest(user=user.username):
                client = authenticate_as(user)
                self.assertRedirects(client.get('/'), landing)
                response = client.get(denied)
                self.assertContains(response, 'Access restricted', status_code=403)
                self.assertNotContains(response, self.ordinary_location.title, status_code=403)
                self.assertNotContains(response, self.restricted.reference, status_code=403)
        self.sam.is_staff = self.sam.is_superuser = True
        self.sam.save(update_fields=['is_staff', 'is_superuser'])
        self.assertEqual(authenticate_as(self.sam).get('/admin/matters/').status_code, 403)

    def test_staff_list_search_detail_deadline_and_api_use_published_projection(self):
        client = authenticate_as(self.sam)
        page = client.get('/portal/')
        self.assertEqual(page.context['metrics'][0]['value'], 2)
        self.assertContains(page, self.ordinary_location.staff_request.title)
        self.assertNotContains(page, 'Awaiting Head')
        self.assertEqual(
            client.get('/portal/requests/', {'q': 'FIN-01'}).context['page_obj'].paginator.count, 0
        )
        self.assertEqual(
            client.get('/portal/requests/', {'q': self.ordinary_location.title})
            .context['page_obj']
            .paginator.count,
            0,
        )
        self.assertEqual(
            client.get('/portal/requests/', {'due': 'upcoming'}).context['page_obj'].paginator.count, 2
        )
        safe_url = reverse('staff_request', args=[self.ordinary_location.pk])
        detail = client.get(safe_url)
        self.assertContains(detail, 'Published test timeline.')
        for private in (
            self.ordinary_location.title,
            self.ordinary_location.internal_summary,
            'Future update marker',
        ):
            self.assertNotContains(detail, private)
        self.assertEqual(client.get(safe_url + '?scope=department').status_code, 403)
        self.assertEqual(
            client.get(reverse('staff_request', args=[self.other_unit.pk])).status_code, 404
        )
        api = client.get('/api/v1/staff/requests/', {'status': StaffStatus.AWAITING})
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()['count'], 2)
        self.assertEqual(
            {result['reference'] for result in api.json()['results']},
            {self.ordinary_location.reference, self.named.reference},
        )
        self.assertEqual(api.json()['results'][0]['status'], 'awaiting')
        self.assertSetEqual(
            set(api.json()['results'][0]),
            {'id', 'reference', 'title', 'status', 'status_label', 'submitted_at', 'due_on'},
        )
        self.assertNotContains(api, 'Confined private summary')
        self.assertEqual(client.get('/api/v1/staff/requests/', {'scope': 'department'}).status_code, 403)
        self.assertEqual(
            client.get('/api/v1/staff/requests/', {'status': 'head_review'}).status_code, 400
        )
        self.assertEqual(client.post('/api/v1/staff/requests/', {}).status_code, 405)

    def test_department_membership_is_not_a_grant_and_hod_sees_only_released_rows(self):
        member = authenticate_as(self.other_staff)
        self.assertEqual(member.get('/portal/?scope=department').status_code, 403)
        hod = authenticate_as(self.hod)
        overview = hod.get('/portal/?scope=department')
        self.assertEqual(overview.context['metrics'][0]['value'], 1)
        self.assertContains(overview, 'Department overview')
        own_scope = hod.get('/portal/')
        self.assertEqual(own_scope.context['metrics'][0]['value'], 0)
        for matter in (self.ordinary_peer, self.other_unit, self.restricted, self.named):
            self.assertEqual(
                hod.get(reverse('staff_request', args=[matter.pk]), {'scope': 'department'}).status_code,
                404,
            )
        released = hod.get(
            reverse('staff_request', args=[self.ordinary_location.pk]), {'scope': 'department'}
        )
        self.assertContains(released, 'Staff-safe test context.')
        self.assertNotContains(released, 'Confined private summary')
        self.assertEqual(hod.get('/api/v1/staff/requests/?scope=department').json()['count'], 1)
        self.hod.department = self.other
        self.hod.save(update_fields=['department'])
        self.assertEqual(hod.get('/portal/?scope=department').status_code, 403)

    def test_legal_scope_search_filters_details_counts_charts_use_same_policy(self):
        officer = authenticate_as(self.officer)
        senior = authenticate_as(self.senior)
        head = authenticate_as(self.head)
        for client, refs in (
            (officer, [self.ordinary_location, self.named]),
            (senior, [self.ordinary_location, self.ordinary_peer]),
            (head, [self.ordinary_location, self.ordinary_peer, self.other_unit]),
        ):
            with self.subTest(expected=[item.reference for item in refs]):
                rows = client.get('/admin/matters/').context['page_obj']
                self.assertCountEqual([item.pk for item in rows], [item.pk for item in refs])
                for matter in (
                    self.ordinary_location,
                    self.ordinary_peer,
                    self.other_unit,
                    self.named,
                    self.restricted,
                    self.demo,
                ):
                    result = client.get(reverse('matter_detail', args=[matter.pk]))
                    self.assertEqual(result.status_code, 200 if matter in refs else 404)
                dashboard = client.get('/admin/')
                self.assertEqual(dashboard.context['metrics'][0]['value'], len(refs))
                self.assertEqual(dashboard.context['chart']['total'], len(refs))
                self.assertEqual(
                    sum(point['value'] for point in dashboard.context['trend']['points']), len(refs)
                )
                self.assertEqual(
                    [item.matter_id for item in dashboard.context['milestones']],
                    [self.ordinary_location.pk],
                )
                api = client.get('/api/v1/legal/summary/').json()
                self.assertEqual(api['metrics'][0]['count'], len(refs))
                self.assertEqual(
                    next(item['count'] for item in api['metrics'] if item['key'] == 'expiries'), 1
                )
        filters = head.get(
            '/admin/', {'directorate': self.other.directorate_id, 'category': 'contract', 'period': '30'}
        )
        self.assertEqual(filters.context['metrics'][0]['value'], 1)
        self.assertEqual(
            head.get('/api/v1/legal/summary/', {'officer': self.officer.pk}).json()['metrics'][0][
                'count'
            ],
            1,
        )
        disallowed = officer.get('/admin/', {'department': self.other.pk})
        self.assertTrue(disallowed.context['filter_form'].errors)
        self.assertEqual(disallowed.context['metrics'][0]['value'], 0)
        self.assertEqual(
            officer.get(
                '/api/v1/legal/summary/', {'directorate': self.other.directorate_id}
            ).status_code,
            400,
        )
        by_date = head.get(
            '/admin/matters/', {'start': str(self.today), 'end': str(self.today - timedelta(days=1))}
        )
        self.assertContains(by_date, 'The end date must be on or after the start date.')
        self.assertContains(by_date, 'No records found')

    def test_privileged_clearance_never_substitutes_for_role_and_expires_or_revokes(self):
        target = reverse('matter_detail', args=[self.named.pk])
        officer = authenticate_as(self.officer)
        self.assertEqual(officer.get(target).status_code, 200)
        for classification, expiry, revoked in [
            (Classification.RESTRICTED, self.now + timedelta(days=2), None),
            (Classification.CONFIDENTIAL, self.now - timedelta(days=2), None),
            (Classification.CONFIDENTIAL, self.now + timedelta(days=2), self.now),
        ]:
            MatterAccess.objects.filter(pk=self.clearance.pk).update(
                classification=classification, expires_at=expiry, revoked_at=revoked
            )
            self.assertEqual(officer.get(target).status_code, 404)
        MatterAccess.objects.create(
            user=self.ict,
            matter=self.named,
            granted_by=self.head,
            classification=Classification.CONFIDENTIAL,
            expires_at=self.now + timedelta(days=1),
        )
        self.assertEqual(authenticate_as(self.ict).get(target).status_code, 403)
        MatterAccess.objects.create(
            user=self.senior,
            matter=self.other_unit,
            granted_by=self.head,
            classification=Classification.INTERNAL,
            expires_at=self.now + timedelta(days=1),
        )
        self.assertEqual(
            authenticate_as(self.senior)
            .get(reverse('matter_detail', args=[self.other_unit.pk]))
            .status_code,
            404,
        )

    def test_expired_future_or_wrong_partition_grants_fail_closed(self):
        loose = employee('multi', self.unit, roles=())
        grant(loose, RoleCode.SENIOR, self.foreign)
        grant(loose, RoleCode.HOD, self.other)
        grant(loose, RoleCode.HEAD, granted_at=self.now + timedelta(days=1))
        grant(loose, RoleCode.OFFICER, expires_at=self.now - timedelta(days=1))
        grant(loose, RoleCode.ICT)
        self.assertFalse(Policy(loose).grants)
        c = authenticate_as(loose)
        self.assertEqual(c.get('/admin/').status_code, 403)
        self.assertEqual(c.get('/portal/').status_code, 403)
        self.assertEqual(len(ROLE_SPECS), Role.objects.count())
        for code, (scope, permissions) in ROLE_SPECS.items():
            role = Role.objects.get(pk=code)
            self.assertEqual(role.scope, scope)
            self.assertCountEqual(role.permissions.values_list('code', flat=True), permissions)
        self.assertGreaterEqual(Category.objects.count(), 11)

    def test_oversight_sees_only_intended_aggregate_or_metadata(self):
        management = authenticate_as(self.manager)
        page = management.get('/admin/management/', {'department': self.unit.pk})
        self.assertEqual([metric['value'] for metric in page.context['metrics']], ['—', '—', '—'])
        self.assertNotContains(page, 'RAD-01')
        self.assertEqual(management.get('/api/v1/legal/summary/').status_code, 403)
        self.assertEqual(management.get('/admin/records/').status_code, 403)
        records = authenticate_as(self.records)
        self.assertContains(records.get('/admin/records/'), 'RAD-01')
        for obscured in (
            self.other_unit.reference,
            self.restricted.reference,
            self.ordinary_location.title,
            self.ordinary_location.internal_summary,
        ):
            self.assertNotContains(records.get('/admin/records/'), obscured)
        self.assertEqual(
            records.get('/admin/records/', {'q': self.restricted.reference})
            .context['page_obj']
            .paginator.count,
            0,
        )
        self.assertEqual(
            records.get(reverse('matter_detail', args=[self.ordinary_location.pk])).status_code, 403
        )
        self.assertEqual(records.get('/api/v1/staff/requests/').status_code, 403)
        self.assertEqual(authenticate_as(self.ict).get('/api/v1/legal/summary/').status_code, 403)

    def test_empty_scopes_anonymous_api_security_and_integrity_constraints(self):
        empty = employee('empty', self.other)
        client = authenticate_as(empty)
        self.assertContains(client.get('/portal/requests/'), 'No records found')
        self.assertContains(client.get('/portal/'), 'No upcoming deadlines')
        self.assertEqual(client.get('/api/v1/staff/requests/').json()['count'], 0)
        anonymous = Client()
        self.assertEqual(anonymous.get('/api/v1/legal/summary/').status_code, 403)
        self.assertEqual(anonymous.get('/api/v1/staff/requests/').status_code, 403)
        self.assertIn('no-store', client.get('/profile/').headers['Cache-Control'])
        self.assertIn(
            "frame-ancestors 'none'", client.get('/profile/').headers['Content-Security-Policy']
        )
        self.assertEqual(client.get('/media/no-access.pdf').status_code, 404)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LegalMatter.objects.create(
                reference=self.ordinary_location.reference, department=self.unit, category_id='contract'
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            LegalRequest.objects.filter(pk=self.ordinary_location.pk).update(public_status='head_review')
        with self.assertRaises(IntegrityError), transaction.atomic():
            LegalAssignment.objects.create(
                matter=self.ordinary_peer,
                officer=self.officer,
                starts_at=self.now,
                ends_at=self.now,
                due_on=self.today,
            )


class DemonstrationTests(TestCase):
    def test_seed_and_shortcut_are_disabled_by_default(self):
        with self.assertRaisesMessage(CommandError, 'isolated'):
            call_command('seed_demo', stdout=StringIO())
        self.assertNotContains(self.client.get('/login/'), 'Explore the demonstration')
        self.assertEqual(self.client.post('/login/demo/', {'role': 'head'}).status_code, 404)

    @override_settings(ENV='development', DEMO_MODE=True, LOCAL_AUTH_ENABLED=False)
    def test_seed_is_repeatable_and_demo_login_is_protected_and_isolated(self):
        for _ in range(2):
            call_command('seed_demo', stdout=StringIO())
        self.assertEqual(LegalMatter.objects.filter(is_demo=True).count(), 106)
        self.assertEqual(User.objects.filter(is_demo=True).count(), 14)
        self.assertEqual(AccessRequest.objects.count(), 2)
        self.assertFalse(any(account.has_usable_password() for account in User.objects.all()))
        for role in RoleCode.values:
            with self.subTest(role=role):
                browser = Client(enforce_csrf_checks=True)
                self.assertContains(browser.get('/login/'), 'Development demonstration')
                self.assertEqual(browser.get('/login/demo/').status_code, 405)
                self.assertEqual(browser.post('/login/demo/', {'role': role}).status_code, 403)
                token = browser.cookies[settings.CSRF_COOKIE_NAME].value
                signed = browser.post(
                    '/login/demo/', {'role': role, 'csrfmiddlewaretoken': token}, follow=True
                )
                self.assertContains(signed, 'Demonstration only')
                self.assertEqual(browser.session['auth_channel'], 'demo')
        employee('not-fixture')
        with self.assertRaisesMessage(CommandError, 'isolated'):
            call_command('seed_demo', stdout=StringIO())
        self.assertNotContains(self.client.get('/login/'), 'Explore the demonstration')
        self.assertEqual(self.client.post('/login/demo/', {'role': 'head'}).status_code, 404)
        self.assertEqual(browser.get('/notifications/').status_code, 302)
