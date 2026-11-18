"""Identity, organisation, scoped grants and audit. Introduced: 2026-11-18; revisions: Git."""

import uuid

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext_lazy as _


class OrgUnit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=14, unique=True)
    name = models.CharField(max_length=100, verbose_name=_('Name (English)'))
    name_sw = models.CharField(max_length=100, blank=True, verbose_name=_('Name (Kiswahili)'))
    is_active = models.BooleanField(default=True, verbose_name=_('Active'))
    is_demo = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ['name']

    def __str__(self):
        return self.name


class Directorate(OrgUnit):
    pass


class Department(OrgUnit):
    directorate = models.ForeignKey(
        Directorate, on_delete=models.PROTECT, related_name='departments', verbose_name=_('Directorate')
    )


class User(AbstractUser):
    class Kind(models.TextChoices):
        EMPLOYEE = 'employee', _('Employee')
        TECHNICAL = 'technical', _('Technical')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=12, choices=Kind, default=Kind.EMPLOYEE)
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.PROTECT, verbose_name=_('Department')
    )
    is_demo = models.BooleanField(default=False, editable=False)
    auth_epoch = models.PositiveIntegerField(default=0, editable=False)
    language = models.CharField(
        max_length=2, choices=[('en', 'English'), ('sw', 'Kiswahili')], default='en'
    )
    show_unread_badge = models.BooleanField(
        default=True, verbose_name=_('Show unread notification count')
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(kind='employee') | Q(department__isnull=True),
                name='identity_tech_no_department',
            )
        ]

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    def _get_session_auth_hash(self, secret=None):
        return salted_hmac(
            'lsms.user.session', f'{self.password}:{self.auth_epoch}', secret=secret, algorithm='sha256'
        ).hexdigest()


class RoleCode(models.TextChoices):
    ICT = 'ict', _('ICT Administrator')
    STAFF = 'staff', _('Staff / Requesting Officer')
    HOD = 'hod', _('Head of Department')
    OFFICER = 'officer', _('Legal Officer')
    SENIOR = 'senior', _('Senior Legal Officer')
    HEAD = 'head', _('Head of Legal Services')
    MANAGEMENT = 'management', _('Management / Director General')
    AUDITOR = 'auditor', _('Internal Auditor')
    RECORDS = 'records', _('Records Officer')


class Scope(models.TextChoices):
    SELF = 'self', _('Own requests')
    ASSIGNED = 'assigned', _('Assigned matters')
    DEPARTMENT = 'department', _('Department')
    INSTITUTION = 'institution', _('Institution')


class Permission(models.Model):
    code = models.CharField(max_length=40, primary_key=True)


class Role(models.Model):
    code = models.CharField(max_length=14, choices=RoleCode, primary_key=True)
    scope = models.CharField(max_length=14, choices=Scope)
    permissions = models.ManyToManyField(Permission, related_name='roles')

    def __str__(self):
        return self.get_code_display()


class RoleGrant(models.Model):
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name='role_grants')
    role = models.ForeignKey(Role, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    scope_key = models.CharField(max_length=36, editable=False)
    granted_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'role', 'scope_key'], name='identity_unique_scoped_grant'
            )
        ]

    def save(self, *args, **kwargs):
        self.scope_key = str(self.department_id or self.role.scope)
        return super().save(*args, **kwargs)


class AccessRequest(models.Model):
    """Two-person role approval; this is not a legal matter workflow."""

    class Status(models.TextChoices):
        PENDING = 'pending', _('Pending decision')
        APPROVED = 'approved', _('Approved for activation')
        REJECTED = 'rejected', _('Rejected')
        APPLIED = 'applied', _('Applied')
        CANCELLED = 'cancelled', _('Cancelled')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    target = models.ForeignKey(User, on_delete=models.PROTECT, related_name='access_requests')
    role = models.ForeignKey(Role, on_delete=models.PROTECT)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    target_epoch = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    reason = models.CharField(max_length=400)
    decision_reason = models.CharField(max_length=400, blank=True)
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='+')
    decided_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    applied_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['target', 'status'])]
        constraints = [
            models.CheckConstraint(
                condition=~Q(target_id=F('requested_by_id')), name='identity_access_not_self_requested'
            ),
            models.CheckConstraint(
                condition=Q(decided_by__isnull=True)
                | (~Q(decided_by_id=F('target_id')) & ~Q(decided_by_id=F('requested_by_id'))),
                name='identity_access_independent_decision',
            ),
            models.CheckConstraint(
                condition=~Q(status__in=['approved', 'applied', 'rejected'])
                | Q(decided_by__isnull=False, decided_at__isnull=False),
                name='identity_access_record_decision',
            ),
            models.CheckConstraint(
                condition=~Q(status='applied')
                | (
                    Q(applied_by__isnull=False, applied_at__isnull=False)
                    & ~Q(applied_by_id=F('decided_by_id'))
                    & ~Q(applied_by_id=F('target_id'))
                ),
                name='identity_access_record_activation',
            ),
        ]


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionDenied('Audit entries are append-only.')

    def delete(self):
        raise PermissionDenied('Audit entries are append-only.')


class AuditLog(models.Model):
    class Area(models.TextChoices):
        IDENTITY = 'identity', _('Identity')
        ORGANISATION = 'organisation', _('Organisation')
        LEGAL = 'legal', _('Legal access')

    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT)
    actor_role = models.CharField(max_length=14, blank=True)
    is_demo = models.BooleanField(default=False, editable=False)
    area = models.CharField(max_length=16, choices=Area)
    action = models.CharField(max_length=42)
    target = models.CharField(max_length=36, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4)
    session_correlation = models.UUIDField(null=True, editable=False)
    source_address = models.GenericIPAddressField(null=True, editable=False)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ['-occurred_at', '-pk']
        indexes = [models.Index(fields=['is_demo', 'area', 'occurred_at'])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionDenied('Audit entries are append-only.')
        # New instances with an explicit existing primary key must never overwrite history.
        kwargs['force_insert'] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied('Audit entries are append-only.')


class AuthAttempt(models.Model):
    key = models.CharField(max_length=64)
    window = models.PositiveBigIntegerField(db_index=True)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['key', 'window'], name='identity_auth_attempt_bucket')
        ]


class TotpDevice(models.Model):
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name='totp_device')
    encrypted_secret = models.BinaryField(editable=False)
    last_step = models.BigIntegerField(default=-1)


class Notification(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.PROTECT, related_name='notifications')
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=350)
    title_sw = models.CharField(max_length=120, blank=True)
    body_sw = models.CharField(max_length=350, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['recipient', 'read_at'])]
