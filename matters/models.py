"""Matter register read-model foundation. Introduced: 2026-11-18; revisions: Git."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Status(models.TextChoices):
    SUBMITTED = 'submitted', _('Submitted')
    PRELIMINARY = 'preliminary', _('Under preliminary review')
    INFORMATION = 'information', _('Awaiting additional information')
    ACCEPTED = 'accepted', _('Accepted')
    ASSIGNED = 'assigned', _('Assigned')
    REVIEW = 'review', _('Under legal review')
    HEAD_REVIEW = 'head_review', _('Awaiting Head of Legal Services review')
    APPROVED = 'approved', _('Approved')
    COMPLETED = 'completed', _('Completed')
    REJECTED = 'rejected', _('Rejected')
    CLOSED = 'closed', _('Closed')


TERMINAL = (Status.COMPLETED, Status.REJECTED, Status.CLOSED)


class StaffStatus(models.TextChoices):
    AWAITING = 'awaiting', _('Awaiting review')
    PROCESSING = 'processing', _('Processing')
    INFORMATION = 'information', _('Needs information')
    COMPLETED = 'completed', _('Completed')
    REJECTED = 'rejected', _('Rejected')
    CLOSED = 'closed', _('Closed')


STAFF_TERMINAL = (StaffStatus.COMPLETED, StaffStatus.REJECTED, StaffStatus.CLOSED)


class Classification(models.TextChoices):
    INTERNAL = 'internal', _('Internal use only')
    CONFIDENTIAL = 'confidential', _('Confidential')
    RESTRICTED = 'restricted', _('Restricted / privileged')


class Category(models.Model):
    code = models.CharField(primary_key=True, max_length=24)
    name = models.CharField(max_length=80)
    name_sw = models.CharField(max_length=80)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class LegalMatter(models.Model):
    """Internal register; Phase 2 transitions are owned by casework commands."""

    class Priority(models.TextChoices):
        LOW = 'low', _('Low')
        NORMAL = 'normal', _('Normal')
        HIGH = 'high', _('High')
        URGENT = 'urgent', _('Urgent')

    class Risk(models.TextChoices):
        LOW = 'low', _('Low risk')
        MEDIUM = 'medium', _('Medium risk')
        HIGH = 'high', _('High risk')
        STRATEGIC = 'strategic', _('Strategic risk')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=36, unique=True)
    title = models.CharField(max_length=160)
    internal_summary = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    department = models.ForeignKey(
        'identity.Department', on_delete=models.PROTECT, related_name='matters'
    )
    status = models.CharField(max_length=16, choices=Status, default=Status.SUBMITTED)
    classification = models.CharField(
        max_length=14, choices=Classification, default=Classification.INTERNAL
    )
    priority = models.CharField(max_length=10, choices=Priority, default=Priority.NORMAL)
    risk = models.CharField(max_length=12, choices=Risk, blank=True)
    opened_at = models.DateTimeField(default=timezone.now)
    is_demo = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ['-opened_at', '-pk']
        indexes = [models.Index(fields=['is_demo', 'department', 'status', 'opened_at'])]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=Status.values), name='matters_valid_status'),
            models.CheckConstraint(
                condition=Q(classification__in=Classification.values),
                name='matters_valid_classification',
            ),
        ]


class LegalRequest(models.Model):
    """Separate staff-safe projection, not a serialization of private matter fields."""

    matter = models.OneToOneField(
        LegalMatter, primary_key=True, on_delete=models.PROTECT, related_name='staff_request'
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='legal_requests'
    )
    title = models.CharField(max_length=160)
    summary = models.TextField(blank=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    due_on = models.DateField(null=True, blank=True)
    department_visible = models.BooleanField(default=False)
    public_status = models.CharField(max_length=16, choices=StaffStatus, default=StaffStatus.AWAITING)

    class Meta:
        ordering = ['-submitted_at', '-pk']
        indexes = [models.Index(fields=['requester', 'submitted_at'])]
        constraints = [
            models.CheckConstraint(
                condition=Q(public_status__in=StaffStatus.values), name='staff_valid_status'
            )
        ]


class RequestUpdate(models.Model):
    request = models.ForeignKey(LegalRequest, on_delete=models.PROTECT, related_name='updates')
    status = models.CharField(max_length=16, choices=StaffStatus)
    message = models.CharField(max_length=240, blank=True)
    message_sw = models.CharField(max_length=240, blank=True)
    published_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-published_at', '-pk']
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=StaffStatus.values), name='staff_valid_update_status'
            )
        ]


class LegalAssignment(models.Model):
    matter = models.ForeignKey(LegalMatter, on_delete=models.PROTECT, related_name='assignments')
    officer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assignments'
    )
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    due_on = models.DateField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['officer', 'ends_at', 'due_on'])]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True) | Q(ends_at__gt=F('starts_at')),
                name='matters_assignment_dates',
            )
        ]


class MatterAccess(models.Model):
    """Additional named clearance; never substitutes for role and base scope."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    matter = models.ForeignKey(LegalMatter, on_delete=models.PROTECT, related_name='clearances')
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    classification = models.CharField(max_length=14, choices=Classification)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'matter'], name='matters_one_named_clearance')
        ]


class Milestone(models.Model):
    class Kind(models.TextChoices):
        HEARING = 'hearing', _('Court hearing')
        EXPIRY = 'expiry', _('Contract expiry')
        RIGHTS = 'rights', _('Rights expiry')
        COMPLIANCE = 'compliance', _('Compliance deadline')
        REASSESS = 'reassess', _('Compliance reassessment')
        CORRECTIVE = 'corrective', _('Corrective action deadline')

    matter = models.ForeignKey(LegalMatter, on_delete=models.PROTECT, related_name='milestones')
    kind = models.CharField(max_length=12, choices=Kind)
    date = models.DateField(db_index=True)
    is_cancelled = models.BooleanField(default=False)

    class Meta:
        ordering = ['date', 'pk']
