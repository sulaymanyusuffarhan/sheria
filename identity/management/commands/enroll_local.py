"""Out-of-band local account enrolment for authorised custodians. Introduced: 2026-11-18; revisions: Git."""

import sys
from getpass import getpass

import pyotp
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from identity.models import AuditLog, Department, Role, RoleCode, RoleGrant, Scope, TotpDevice, User
from identity.policy import Policy
from identity.services import invalidate_access


class Command(BaseCommand):
    help = (
        'Bootstrap a non-demo account or enrol a provisioned employee locally in a controlled terminal.'
    )

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument(
            '--new-role', choices=RoleCode.values, help='Required only when bootstrapping a new account.'
        )
        parser.add_argument(
            '--department',
            help='Existing department code for a new account and any department-scoped role.',
        )
        parser.add_argument(
            '--totp', action='store_true', help='Enrol a TOTP device (required for elevated roles).'
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if (
            settings.DEMO_MODE
            or not settings.LOCAL_AUTH_ENABLED
            or User.objects.filter(is_demo=True).exists()
        ):
            raise CommandError('Local enrolment is disabled here.')
        if not sys.stdin.isatty():
            raise CommandError('Credential enrolment must run in a private, interactive terminal.')
        username = options['username'].strip().lower()
        if (
            not username
            or len(username) > 80
            or any(ch not in 'abcdefghijklmnopqrstuvwxyz0123456789._-' for ch in username)
        ):
            raise CommandError('Enter a valid lowercase username.')
        user = User.objects.select_for_update().filter(username=username, is_demo=False).first()
        if not user:
            role_code = options['new_role']
            if not role_code:
                raise CommandError('No provisioned employee found. Bootstrapping requires --new-role.')
            role = Role.objects.get(pk=role_code)
            dept_code = options['department']
            dept = (
                Department.objects.filter(
                    code=dept_code, is_demo=False, is_active=True, directorate__is_active=True
                ).first()
                if dept_code
                else None
            )
            if dept_code and not dept:
                raise CommandError('Department not found or inactive.')
            if (role.scope == Scope.DEPARTMENT and not dept) or (role_code == RoleCode.ICT and dept):
                raise CommandError('Department scope is incompatible with this role.')
            user = User(
                username=username,
                department=dept,
                kind=User.Kind.TECHNICAL if role_code == RoleCode.ICT else User.Kind.EMPLOYEE,
            )
            user.set_unusable_password()
            user.save()
            RoleGrant.objects.create(
                user=user, role=role, department=dept if role.scope == Scope.DEPARTMENT else None
            )
        elif options['new_role']:
            raise CommandError('Existing accounts cannot change roles through enrolment.')
        if not user.is_active:
            raise CommandError('Do not enrol a disabled account.')
        needs_mfa = Policy(user).requires_mfa
        if needs_mfa and not options['totp'] and not TotpDevice.objects.filter(user=user).exists():
            raise CommandError(
                'Enrol a separate authenticator with --totp and an environment-managed LSMS_MFA_KEY.'
            )
        password = getpass('Account holder password (not saved in plaintext): ')
        if password != getpass('Confirm password: '):
            raise CommandError('Passwords do not match.')
        if len(password) > 256:
            raise CommandError('Use at most 256 characters.')
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError(' '.join(exc.messages)) from exc
        if options['totp']:
            try:
                cipher = Fernet(settings.MFA_KEY.encode('ascii'))
            except ValueError as exc:
                raise CommandError('Configure a valid environment-managed LSMS_MFA_KEY.') from exc
            secret = pyotp.random_base32()
            # This command must not be run in CI or with captured/shared terminal output.
            self.stdout.write(f'Add this key in the account holder’s authenticator: {secret}')
            response = getpass('Verify current six-digit authenticator code: ').strip()
            totp = pyotp.TOTP(secret)
            step = int(timezone.now().timestamp() // totp.interval)
            if not totp.verify(response):
                raise CommandError('Authenticator verification failed.')
            TotpDevice.objects.update_or_create(
                user=user,
                defaults={'encrypted_secret': cipher.encrypt(secret.encode()), 'last_step': step},
            )
        user.set_password(password)
        user.save(update_fields=['password'])
        invalidate_access(user)
        AuditLog.objects.create(
            area=AuditLog.Area.IDENTITY,
            action='credentials_enrolled',
            target=str(user.pk),
            detail={'channel': 'custodian_terminal'},
        )
        self.stdout.write(
            self.style.SUCCESS('Local credentials enrolled. Previous sessions were revoked.')
        )
