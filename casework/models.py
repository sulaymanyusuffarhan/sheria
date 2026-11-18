"""Phase 2 transactions, private evidence and immutable review history. Revisions: Git."""

import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from matters.models import LegalMatter, Status


class HistoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionDenied('Recorded workflow history cannot be changed.')

    def delete(self):
        raise PermissionDenied('Recorded workflow history cannot be changed.')


class Recorded(models.Model):
    objects = HistoryQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionDenied('Recorded workflow history cannot be changed.')
        kwargs['force_insert'] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied('Recorded workflow history cannot be changed.')


class ReferenceCounter(models.Model):
    namespace = models.CharField(max_length=12)
    year = models.PositiveSmallIntegerField()
    last_number = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('namespace', 'year'), name='casework_number_scope')
        ]


class Case(models.Model):
    """One active workflow per newly submitted matter. Legacy read rows do not get invented history."""

    matter = models.OneToOneField(
        LegalMatter, primary_key=True, on_delete=models.PROTECT, related_name='workflow'
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    submission_key = models.UUIDField()
    submission_digest = models.CharField(max_length=64)
    # Immutable at the service boundary: submission-time contacts, department and declaration.
    submission = models.JSONField()
    revision = models.PositiveIntegerField(default=1)
    policy_revision = models.PositiveSmallIntegerField(default=1)
    internal = models.BooleanField(default=False, editable=False)
    minimum_version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('submitted_by', 'submission_key'), name='casework_submission_once'
            )
        ]


class Event(Recorded):
    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name='events')
    revision = models.PositiveIntegerField()
    command = models.CharField(max_length=24)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    before = models.CharField(max_length=16, choices=Status, blank=True)
    after = models.CharField(max_length=16, choices=Status)
    reason = models.CharField(max_length=1600, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-revision']
        constraints = [
            models.UniqueConstraint(fields=('case', 'revision'), name='casework_event_revision')
        ]


class InformationRound(models.Model):
    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name='information_rounds')
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    requested_at = models.DateTimeField(default=timezone.now)
    question = models.CharField(max_length=240)
    due_on = models.DateField()
    resume_status = models.CharField(max_length=16, choices=Status)
    reply = models.CharField(max_length=1600, blank=True)
    replied_at = models.DateTimeField(null=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True
    )
    verified_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ['-pk']


class Document(models.Model):
    class Kind(models.TextChoices):
        SUBMISSION = 'submission', _('Request attachment')
        DRAFT = 'draft', _('Contract draft')
        INTERNAL = 'internal', _('Internal evidence')
        RESPONSE = 'response', _('Proposed deliverable')
        EXECUTION = 'execution', _('Signed counterpart')
        REPOSITORY = 'repository', _('Repository version')

    class Scan(models.TextChoices):
        PENDING = 'pending', _('Quarantined — awaiting scan')
        CLEAN = 'clean', _('Available')
        BLOCKED = 'blocked', _('Blocked')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name='documents')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=12, choices=Kind)
    name = models.CharField(max_length=120)
    media_type = models.CharField(max_length=90)
    byte_size = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)
    scan = models.CharField(max_length=8, choices=Scan)
    scan_method = models.CharField(max_length=24)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.CheckConstraint(
                condition=Q(scan__in=['clean', 'pending', 'blocked']), name='casework_document_scan'
            )
        ]

    def __str__(self):
        return self.name


class DocumentBlob(Recorded):
    document = models.OneToOneField(
        Document, primary_key=True, on_delete=models.PROTECT, related_name='blob'
    )
    ciphertext = models.BinaryField(editable=False)


class ContractType(models.TextChoices):
    PROCUREMENT = 'procurement', _('Procurement')
    CONTENT = 'content', _('Content acquisition / production')
    SPONSORSHIP = 'sponsorship', _('Sponsorship')
    ADVERTISING = 'advertising', _('Advertising')
    ICT = 'ict', _('ICT / digital services')
    PPP = 'ppp', _('Public–private partnership')
    LEASE = 'lease', _('Lease')
    CONSULTANCY = 'consultancy', _('Consultancy')
    MOU = 'mou', _('Memorandum of Understanding')
    EMPLOYMENT = 'employment', _('Employment-related')
    BROADCAST = 'broadcasting', _('Broadcasting rights')
    OTHER = 'other', _('Other contract type')


class Contract(Recorded):
    case = models.OneToOneField(
        Case, primary_key=True, on_delete=models.PROTECT, related_name='contract'
    )
    reference = models.CharField(max_length=36, unique=True)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    registered_at = models.DateTimeField(default=timezone.now)


class ContractVersion(Recorded):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name='versions')
    number = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    title = models.CharField(max_length=160)
    counterparty = models.CharField(max_length=160)
    contract_type = models.CharField(max_length=16, choices=ContractType)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    currency = models.CharField(max_length=3)
    duration = models.CharField(max_length=100)
    reason = models.CharField(max_length=400)
    draft = models.ForeignKey(Document, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['-number']
        constraints = [
            models.UniqueConstraint(fields=('contract', 'number'), name='casework_contract_version'),
            models.CheckConstraint(condition=Q(amount__gte=0), name='casework_contract_amount'),
        ]


class Checklist(models.Model):
    class Kind(models.TextChoices):
        PRE = 'pre', _('Pre-clearance review')
        POST = 'post', _('Post-execution review')

    version = models.ForeignKey(ContractVersion, on_delete=models.PROTECT, related_name='checklists')
    kind = models.CharField(max_length=4, choices=Kind)
    template_revision = models.PositiveSmallIntegerField(default=1)
    # Bounded schema validated at each save; stable IDs and versioned prompts live in checklist.py.
    answers = models.JSONField(default=dict)
    risks = models.JSONField(default=list)
    risk_class = models.CharField(max_length=12, choices=LegalMatter.Risk, blank=True)
    post_details = models.JSONField(default=dict)
    finalized_at = models.DateTimeField(null=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('version', 'kind'), name='casework_checklist_instance')
        ]


class Recommendation(models.TextChoices):
    APPROVE = 'approve', _('Approved – Legally Acceptable')
    CONDITIONAL = 'conditional', _('Approved Subject to Amendments')
    NEGOTIATE = 'negotiate', _('Requires Further Negotiation')
    REJECT = 'reject', _('Not Recommended for Approval')


class ReviewRound(Recorded):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name='reviews')
    number = models.PositiveIntegerField()
    version = models.ForeignKey(
        ContractVersion, on_delete=models.PROTECT, related_name='reviews', null=True
    )
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    submitted_at = models.DateTimeField(default=timezone.now)
    recommendation = models.CharField(max_length=12, choices=Recommendation)
    package = models.JSONField()
    digest = models.CharField(max_length=64)

    class Meta:
        ordering = ['-number']
        constraints = [models.UniqueConstraint(fields=('case', 'number'), name='casework_review_round')]


class Decision(Recorded):
    class Outcome(models.TextChoices):
        CLEAR = 'clear', _('Approve / clear this revision')
        RETURN = 'return', _('Return for changes')
        CONDITIONAL = 'conditional', _('Conditional — not cleared')
        REJECT = 'reject', _('Reject / not recommended')

    review = models.OneToOneField(ReviewRound, on_delete=models.PROTECT, related_name='decision')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    decided_name = models.CharField(max_length=160)
    decided_at = models.DateTimeField(default=timezone.now)
    assurance = models.CharField(max_length=16)
    outcome = models.CharField(max_length=12, choices=Outcome)
    reason = models.CharField(max_length=1600)
    conditions = models.CharField(max_length=700, blank=True)
    condition_due = models.DateField(null=True)
    condition_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True
    )
    gate_evidence = models.ForeignKey(Document, on_delete=models.PROTECT, related_name='+', null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(outcome='conditional')
                | (Q(condition_due__isnull=False, condition_owner__isnull=False) & ~Q(conditions='')),
                name='casework_decision_conditions',
            )
        ]


class Release(Recorded):
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name='releases')
    decision = models.ForeignKey(Decision, on_delete=models.PROTECT, related_name='releases')
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    released_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('document', 'decision'), name='casework_release_once')
        ]


class Execution(Recorded):
    version = models.OneToOneField(ContractVersion, on_delete=models.PROTECT, related_name='execution')
    clearance = models.OneToOneField(Decision, on_delete=models.PROTECT, related_name='execution')
    signed_copy = models.OneToOneField(Document, on_delete=models.PROTECT, related_name='execution')
    signed_on = models.DateField()
    effective_on = models.DateField()
    expires_on = models.DateField(null=True)
    signatories = models.CharField(max_length=350)
    prerequisites = models.CharField(max_length=1200)
    assurance = models.CharField(max_length=16)
    recorded_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(expires_on__isnull=True) | Q(expires_on__gte=F('effective_on')),
                name='casework_execution_dates',
            )
        ]


class EventNotice(models.Model):
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='notices')
    notification = models.OneToOneField(
        'identity.Notification', on_delete=models.PROTECT, related_name='workflow_notice'
    )
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    staff_audience = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('event', 'recipient'), name='casework_notice_once')
        ]
