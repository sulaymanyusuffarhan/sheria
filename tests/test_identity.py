"""Phase 1 IAM, credential and access-governance verification. Revisions: Git."""

import secrets
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pyotp
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from identity.models import (
    AccessRequest,
    AuditLog,
    AuthAttempt,
    Department,
    Directorate,
    Notification,
    RoleCode,
    RoleGrant,
    TotpDevice,
    User,
)
from identity.policy import Policy
from matters.models import LegalAssignment, LegalMatter, MatterAccess

from .factories import authenticate_as, division, employee, grant


class AuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = secrets.token_urlsafe(28)
        cls.staff = employee('employee', password=cls.password)
        cls.head = employee('head', roles=(RoleCode.HEAD,), password=cls.password)

    def login(self, client=None, user=None, **fields):
        client = client if client is not None else self.client
        return client.post(
            reverse('login'),
            {
                'username': (user or self.staff).username.upper(),
                'password': self.password,
                **fields,
            },
        )

    def test_local_login_session_rotation_audit_and_logout(self):
        session = self.client.session
        session['pre_auth'] = True
        session.save()
        old_id = session.session_key
        response = self.login(next='/notifications/')
        self.assertRedirects(response, '/notifications/')
        self.assertNotEqual(old_id, self.client.session.session_key)
        self.assertNotIn('pre_auth', self.client.session)
        event = AuditLog.objects.get(action='signed_in')
        self.assertEqual(event.actor, self.staff)
        self.assertEqual(event.source_address, '127.0.0.1')
        self.assertEqual(str(event.session_correlation), self.client.session['audit_session'])
        self.assertEqual(event.detail, {'mfa': False})
        self.assertTrue(self.staff.password.startswith('argon2$'))
        self.assertEqual(self.client.get('/logout/').status_code, 405)
        self.assertRedirects(self.client.post('/logout/'), '/login/')
        self.assertEqual(
            AuditLog.objects.get(action='signed_out').session_correlation, event.session_correlation
        )
        self.assertRedirects(self.client.get('/portal/'), '/login/?next=/portal/')

    def test_login_invalid_inactive_and_closed_redirect(self):
        self.assertContains(self.login(password='wrong'), 'Sign-in failed.')
        self.staff.is_active = False
        self.staff.save(update_fields=['is_active'])
        self.assertContains(self.login(), 'Sign-in failed.')
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(AuditLog.objects.filter(action='sign_in_failed').count(), 2)
        self.staff.is_active = True
        self.staff.save(update_fields=['is_active'])
        self.assertEqual(self.login(next='//outside.example.invalid/path').url, '/')

    def test_protected_auth_and_write_forms_have_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.assertContains(self.login(client), 'Form could not be verified', status_code=403)
        client.get('/login/', secure=True)
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        bad = client.post(
            '/login/',
            {'username': self.staff.username, 'password': self.password, 'csrfmiddlewaretoken': token},
            secure=True,
            HTTP_ORIGIN='https://unexpected.example.invalid',
        )
        self.assertEqual(bad.status_code, 403)
        signed = client.post(
            '/login/',
            {'username': self.staff.username, 'password': self.password, 'csrfmiddlewaretoken': token},
            secure=True,
            HTTP_ORIGIN='https://testserver',
        )
        self.assertEqual(signed.status_code, 302)
        self.assertTrue(signed.cookies[settings.CSRF_COOKIE_NAME]['httponly'])
        self.assertEqual(client.post('/profile/', {'language': 'sw'}).status_code, 403)
        self.assertEqual(client.post('/logout/').status_code, 403)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.language, 'en')

    def test_throttle_holds_even_when_credentials_are_correct(self):
        for _ in range(7):
            self.assertContains(self.login(password='incorrect'), 'Sign-in failed.')
        self.assertContains(self.login(), 'Too many sign-in attempts')
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(AuthAttempt.objects.count(), 2)
        self.assertFalse(AuthAttempt.objects.filter(key__contains=self.staff.username).exists())

    def test_elevated_login_requires_fresh_totp(self):
        cipher = Fernet.generate_key()
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        device = TotpDevice.objects.create(
            user=self.head, encrypted_secret=Fernet(cipher).encrypt(secret.encode())
        )
        with override_settings(MFA_KEY=cipher.decode()):
            self.assertContains(self.login(user=self.head), 'Sign-in failed.')
            self.assertContains(self.login(user=self.head, code='xxxxxx'), 'Sign-in failed.')
            code = totp.now()
            self.assertRedirects(
                self.login(user=self.head, code=code), '/', fetch_redirect_response=False
            )
            self.assertEqual(self.client.get('/admin/').status_code, 200)
            device.refresh_from_db()
            self.assertGreater(device.last_step, 0)
            self.assertTrue(self.client.session['verified_mfa'])
            self.assertTrue(AuditLog.objects.get(action='signed_in').detail['mfa'])
            self.assertContains(self.login(Client(), user=self.head, code=code), 'Sign-in failed.')
        self.assertNotIn(secret.encode(), bytes(device.encrypted_secret))

    def test_mfa_missing_at_session_and_idle_or_absolute_expiry(self):
        client = authenticate_as(self.head, verified_mfa=False)
        self.assertRedirects(client.get('/admin/'), '/login/?next=/admin/')
        self.assertEqual(client.get('/api/v1/legal/summary/').status_code, 403)
        for key, limit in [
            ('signed_in_at', settings.SESSION_COOKIE_AGE),
            ('last_activity_at', settings.SESSION_IDLE_SECONDS),
        ]:
            with self.subTest(key=key):
                client = authenticate_as(self.staff)
                session = client.session
                session[key] -= limit + 1
                session.save()
                self.assertRedirects(client.get('/portal/'), '/login/?next=/portal/')
        self.assertEqual(AuditLog.objects.filter(action='session_expired').count(), 3)

    def test_password_change_preserves_one_session_and_revokes_the_other(self):
        client_a = authenticate_as(self.staff)
        client_b = authenticate_as(self.staff)
        url = reverse('change_password')
        proposed = secrets.token_urlsafe(28)
        self.assertContains(
            client_a.post(url, {'current': 'bad', 'new': proposed, 'confirm': 'different'}),
            'The passwords do not match.',
        )
        self.assertRedirects(
            client_a.post(url, {'current': self.password, 'new': proposed, 'confirm': proposed}),
            '/profile/',
        )
        self.assertEqual(client_a.get('/portal/').status_code, 200)
        self.assertRedirects(client_b.get('/portal/'), '/login/?next=/portal/')
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password(proposed))
        self.assertEqual(AuditLog.objects.get(action='password_changed').actor, self.staff)

    def test_enrolment_existing_employee_is_terminal_only_and_audited(self):
        account = employee('waiting')
        password = secrets.token_urlsafe(28)
        stream = StringIO()
        with patch('identity.management.commands.enroll_local.sys.stdin.isatty', return_value=False):
            with self.assertRaisesMessage(CommandError, 'private, interactive terminal'):
                call_command('enroll_local', account.username, stdout=stream)
        with (
            patch('identity.management.commands.enroll_local.sys.stdin.isatty', return_value=True),
            patch('identity.management.commands.enroll_local.getpass', side_effect=[password, password]),
        ):
            call_command('enroll_local', account.username, stdout=stream)
        self.assertNotIn(password, stream.getvalue())
        self.assertRedirects(
            self.login(user=account, password=password), '/', fetch_redirect_response=False
        )
        self.assertEqual(AuditLog.objects.get(action='credentials_enrolled').target, str(account.pk))
        self.assertFalse(AuditLog.objects.filter(detail__channel=password).exists())

    @override_settings(LOCAL_AUTH_ENABLED=False)
    def test_production_style_config_refuses_local_auth_and_enrolment(self):
        self.assertContains(self.login(), 'Institutional sign-in is not configured')
        self.assertFalse(AuditLog.objects.filter(action='signed_in').exists())
        with self.assertRaisesMessage(CommandError, 'Local enrolment is disabled'):
            call_command('enroll_local', self.staff.username)
        self.assertEqual(
            self.client.post(reverse('demo_login'), {'role': RoleCode.STAFF}).status_code, 404
        )


class IdentityAdministrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.unit = division('NEWS')
        cls.other = division('TECH')
        cls.ict = employee('it-operator', roles=(RoleCode.ICT,))
        cls.head = employee('governor', cls.unit, roles=(RoleCode.HEAD,))
        cls.hire = employee('hire', cls.unit)
        cls.auditor = employee('auditor', roles=(RoleCode.AUDITOR,))

    def setUp(self):
        self.ict_client = authenticate_as(self.ict)
        self.head_client = authenticate_as(self.head)

    def account_data(self, **overrides):
        return {
            'username': self.hire.username,
            'first_name': 'Test',
            'last_name': 'Person',
            'email': 'person@example.invalid',
            'department': self.unit.pk,
            'is_active': 'on',
            **overrides,
        }

    def request_access(self, target=None, *, code=RoleCode.OFFICER, department=''):
        target = target or self.hire
        return self.ict_client.post(
            reverse('access_new', args=[target.pk]),
            {
                'role': code,
                'department': department,
                'reason': 'Verified test assignment.',
            },
        )

    def test_identity_forms_persist_validated_data_only(self):
        url = reverse('employee_new')
        self.assertContains(self.ict_client.post(url, {}), 'This field is required.')
        self.assertContains(
            self.ict_client.post(url, self.account_data()), 'That username is already in use.'
        )
        response = self.ict_client.post(
            url, self.account_data(username='new-employee', kind='technical', is_superuser=True)
        )
        new = User.objects.get(username='new-employee')
        self.assertRedirects(response, reverse('account_detail', args=[new.pk]))
        self.assertEqual(new.kind, User.Kind.EMPLOYEE)
        self.assertFalse(new.is_superuser or new.has_usable_password())
        self.assertEqual([item.role_id for item in Policy(new).grants], [RoleCode.STAFF])
        self.assertEqual(self.head_client.get(url).status_code, 403)
        self.assertEqual(self.ict_client.post(reverse('accounts'), {}).status_code, 405)
        update = self.account_data(first_name='Updated', email='other@example.invalid')
        self.assertEqual(
            self.ict_client.post(reverse('employee_edit', args=[new.pk]), update).status_code, 302
        )
        new.refresh_from_db()
        self.assertEqual(
            (new.username, new.first_name, new.email),
            ('new-employee', 'Updated', 'other@example.invalid'),
        )
        self.assertTrue(AuditLog.objects.filter(action='employee_created', target=str(new.pk)).exists())

    def test_directory_create_rename_and_deactivation_guards(self):
        new_directorate = reverse('directory_new', args=['directorate'])
        response = self.ict_client.post(
            new_directorate,
            {'code': 'ENG', 'name': 'Engineering', 'name_sw': 'Uhandisi', 'is_active': 'on'},
        )
        self.assertRedirects(response, '/admin/directory/')
        parent = Directorate.objects.get(code='ENG')
        path = reverse('directory_new', args=['department'])
        self.assertRedirects(
            self.ict_client.post(
                path, {'code': 'FIELD', 'name': 'Field', 'directorate': parent.pk, 'is_active': 'on'}
            ),
            '/admin/directory/',
        )
        dept = Department.objects.get(code='FIELD')
        self.assertEqual(dept.directorate, parent)
        edit_dept = reverse('directory_edit', args=['department', dept.pk])
        self.ict_client.post(
            edit_dept,
            {
                'code': 'MUTATED',
                'name': 'Field Services',
                'directorate': self.other.directorate_id,
                'is_active': 'on',
            },
        )
        dept.refresh_from_db()
        self.assertEqual((dept.code, dept.name, dept.directorate), ('FIELD', 'Field Services', parent))
        edit_parent = reverse('directory_edit', args=['directorate', parent.pk])
        self.assertContains(
            self.ict_client.post(edit_parent, {'name': parent.name}), 'Move or deactivate active members'
        )
        self.assertRedirects(self.ict_client.post(edit_dept, {'name': dept.name}), '/admin/directory/')
        self.assertRedirects(
            self.ict_client.post(edit_parent, {'name': parent.name}), '/admin/directory/'
        )
        self.assertContains(
            self.ict_client.post(edit_dept, {'name': dept.name, 'is_active': 'on'}),
            'Select an active directorate.',
        )
        live = reverse('directory_edit', args=['department', self.unit.pk])
        self.assertContains(
            self.ict_client.post(live, {'name': self.unit.name}), 'Move or deactivate active members'
        )

    def test_access_decision_activation_revocation_are_separate(self):
        target_session = authenticate_as(self.hire)
        self.assertEqual(self.request_access().status_code, 302)
        item = AccessRequest.objects.get()
        detail, apply = reverse('access_detail', args=[item.pk]), reverse('access_apply', args=[item.pk])
        valid_decision = {'decision': 'approved', 'reason': 'Independent reviewer agreed.'}
        self.assertContains(self.request_access(), 'already an open request')
        self.assertEqual(self.ict_client.post(detail, valid_decision).status_code, 403)
        self.assertEqual(self.ict_client.get(apply).status_code, 405)
        self.assertEqual(self.head_client.post(apply).status_code, 403)
        self.assertEqual(self.ict_client.post(apply).status_code, 302)
        self.assertEqual(AccessRequest.objects.get(pk=item.pk).status, 'pending')
        self.assertRedirects(self.head_client.post(detail, valid_decision), detail)
        item.refresh_from_db()
        self.assertEqual((item.status, item.decided_by), ('approved', self.head))
        self.assertFalse(Policy(self.hire).has('matter.assigned'))
        self.assertRedirects(self.ict_client.post(apply), detail)
        item.refresh_from_db()
        self.assertEqual((item.status, item.applied_by), ('applied', self.ict))
        self.assertTrue(Policy(self.hire).has('matter.assigned'))
        self.assertRedirects(target_session.get('/portal/'), '/login/?next=/portal/')
        self.assertRedirects(self.ict_client.post(apply), detail)
        self.assertEqual(RoleGrant.objects.filter(user=self.hire, role_id=RoleCode.OFFICER).count(), 1)
        grant_to_revoke = RoleGrant.objects.get(user=self.hire, role_id=RoleCode.OFFICER)
        revoke = reverse('access_revoke', args=[grant_to_revoke.pk])
        self.assertEqual(self.ict_client.get(revoke).status_code, 405)
        self.ict_client.post(revoke)
        self.assertFalse(Policy(self.hire).has('matter.assigned'))
        events = AuditLog.objects.filter(
            action__in=['access_requested', 'access_decided', 'access_applied', 'access_revoked']
        ).values_list('action', flat=True)
        self.assertCountEqual(
            events, ['access_requested', 'access_decided', 'access_applied', 'access_revoked']
        )

    def test_invalid_roles_scope_and_malformed_decisions_are_rejected(self):
        self.assertEqual(self.request_access(self.ict).status_code, 404)
        self.assertContains(self.request_access(code=RoleCode.HEAD), 'Select a valid choice.')
        self.assertContains(
            self.request_access(code=RoleCode.HOD, department=self.other.pk),
            'must belong to the selected department',
        )
        self.assertContains(
            self.request_access(code=RoleCode.SENIOR), 'Select an active department for this role.'
        )
        self.assertContains(self.request_access(department=self.unit.pk), 'Leave the department blank')
        self.assertFalse(AccessRequest.objects.exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            AccessRequest.objects.create(
                target=self.hire, requested_by=self.hire, target_epoch=0, role_id=RoleCode.OFFICER
            )
        self.request_access()
        item = AccessRequest.objects.get()
        path = reverse('access_detail', args=[item.pk])
        self.assertContains(
            self.head_client.post(path, {'decision': 'approved'}), 'This field is required.'
        )
        rejected = self.head_client.post(
            path, {'decision': 'rejected', 'reason': 'No active assignment.'}
        )
        self.assertRedirects(rejected, path)
        self.ict_client.post(reverse('access_apply', args=[item.pk]))
        self.assertEqual(AccessRequest.objects.get(pk=item.pk).status, 'rejected')
        self.assertFalse(Policy(self.hire).has('matter.assigned'))

    def test_offboarding_invalidates_sessions_grants_pending_access_and_clearances(self):
        grant(self.hire, RoleCode.OFFICER)
        item = LegalMatter.objects.create(
            reference='OFFBOARD-FIXTURE',
            title='Synthetic matter',
            category_id='contract',
            department=self.unit,
        )
        assignment = LegalAssignment.objects.create(
            matter=item, officer=self.hire, due_on=timezone.localdate()
        )
        clearance = MatterAccess.objects.create(
            matter=item,
            user=self.hire,
            granted_by=self.head,
            classification='restricted',
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.request_access(code=RoleCode.HOD, department=self.unit.pk)
        previous = authenticate_as(self.hire)
        self.assertEqual(previous.get(reverse('matter_detail', args=[item.pk])).status_code, 200)
        url = reverse('employee_edit', args=[self.hire.pk])
        disabled = self.account_data()
        disabled.pop('is_active')
        self.assertEqual(self.ict_client.post(url, disabled).status_code, 302)
        self.hire.refresh_from_db()
        self.assertFalse(self.hire.is_active)
        self.assertEqual(previous.get('/api/v1/legal/summary/').status_code, 403)
        self.assertEqual(AccessRequest.objects.get().status, 'cancelled')
        assignment.refresh_from_db()
        clearance.refresh_from_db()
        self.assertIsNotNone(assignment.revoked_at)
        self.assertIsNotNone(clearance.revoked_at)
        self.ict_client.post(url, self.account_data())
        self.hire.refresh_from_db()
        self.assertTrue(self.hire.is_active)
        self.assertFalse(Policy(self.hire).has('matter.assigned'))
        self.assertTrue(Policy(self.hire).has('request.own'))
        self.assertRedirects(previous.get('/portal/'), '/login/?next=/portal/')

    def test_approval_cannot_be_activated_after_approver_loses_authority(self):
        self.request_access()
        item = AccessRequest.objects.get()
        self.head_client.post(
            reverse('access_detail', args=[item.pk]),
            {'decision': 'approved', 'reason': 'Test rationale.'},
        )
        self.head.is_active = False
        self.head.save(update_fields=['is_active'])
        response = self.ict_client.post(reverse('access_apply', args=[item.pk]), follow=True)
        self.assertContains(response, 'no longer has decision authority')
        self.assertFalse(Policy(self.hire).has('matter.assigned'))

    def test_audit_is_append_only_and_redacted(self):
        legal = AuditLog.objects.create(
            area=AuditLog.Area.LEGAL,
            actor=self.head,
            action='matter_viewed',
            target='fixture-target',
            detail={'private': 'private-value'},
        )
        AuditLog.objects.create(
            area=AuditLog.Area.IDENTITY,
            actor=self.ict,
            action='employee_created',
            source_address='192.0.2.1',
        )
        audit = reverse('audit_log')
        ict_view = self.ict_client.get(audit)
        self.assertNotContains(ict_view, 'Matter opened')
        self.assertNotContains(ict_view, '192.0.2.1')
        self.assertContains(ict_view, 'Employee account created')
        auditor = authenticate_as(self.auditor)
        audited = auditor.get(audit)
        self.assertContains(audited, 'Matter opened')
        self.assertNotContains(audited, 'fixture-target')
        self.assertNotContains(audited, 'private-value')
        self.assertNotContains(audited, '192.0.2.1')
        self.assertEqual(auditor.post(audit, {'action': 'delete'}).status_code, 405)
        self.assertEqual(self.head_client.get(audit).status_code, 403)
        legal.action = 'changed'
        with self.assertRaises(PermissionDenied):
            legal.save()
        with self.assertRaises(PermissionDenied):
            legal.delete()
        with self.assertRaises(PermissionDenied):
            AuditLog.objects.filter(pk=legal.pk).update(action='changed')
        with self.assertRaises(PermissionDenied):
            AuditLog.objects.filter(pk=legal.pk).delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AuditLog(pk=legal.pk, action='injected', area=AuditLog.Area.LEGAL).save()
        self.assertEqual(AuditLog.objects.get(pk=legal.pk).action, 'matter_viewed')

    def test_preferences_language_switch_and_inbox_writes_are_scoped(self):
        client = authenticate_as(self.hire)
        own = Notification.objects.create(
            recipient=self.hire, title='Fixture notice', title_sw='Arifa ya jaribio', body='Test body.'
        )
        private = Notification.objects.create(
            recipient=self.head, title='Another recipient', body='Private recipient.'
        )
        self.assertContains(client.get('/notifications/'), 'Fixture notice')
        self.assertNotContains(client.get('/notifications/'), private.title)
        own_url = reverse('mark_read', args=[own.pk])
        self.assertEqual(client.get(own_url).status_code, 405)
        self.assertEqual(client.post(reverse('mark_read', args=[private.pk])).status_code, 404)
        self.assertRedirects(client.post(own_url), '/notifications/')
        own.refresh_from_db()
        private.refresh_from_db()
        self.assertIsNotNone(own.read_at)
        self.assertIsNone(private.read_at)
        self.assertRedirects(client.post('/profile/', {'language': 'sw'}), '/profile/')
        self.assertContains(client.get('/profile/'), 'Wasifu wangu')
        self.assertContains(client.get('/notifications/'), 'Arifa ya jaribio')
        self.assertContains(client.get('/notifications/'), '<html lang="sw">')
        self.hire.refresh_from_db()
        self.assertEqual((self.hire.language, self.hire.show_unread_badge), ('sw', False))
        self.assertRedirects(
            client.post('/language/', {'language': 'en', 'next': '/portal/'}), '/portal/'
        )
        self.assertContains(client.get('/portal/'), '<html lang="en">')
        response = client.post(
            '/language/', {'language': 'sw', 'next': 'https://outside.example.invalid/path'}
        )
        self.assertNotIn('outside.example.invalid', response.headers.get('Location', ''))
