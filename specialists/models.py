"""Specialist records, keyed to the central case and versioned evidence. Revisions: Git."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from casework.models import Case, Document, Execution, Recorded, ReviewRound
from matters.models import Milestone

USER = settings.AUTH_USER_MODEL


class Dossier(Recorded):
    case = models.OneToOneField(
        Case, on_delete=models.PROTECT, primary_key=True, related_name='%(class)s'
    )
    reference = models.CharField(max_length=36, unique=True)
    registered_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    registered_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


class Litigation(Dossier):
    court = models.CharField(max_length=140)
    external_number = models.CharField(max_length=85, blank=True)
    filed_on = models.DateField(null=True)
    case_type = models.CharField(max_length=100)
    advocate = models.CharField(max_length=160, blank=True)


class Party(Recorded):
    class Role(models.TextChoices):
        CLAIMANT = 'claimant', _('Claimant / appellant')
        RESPONDENT = 'respondent', _('Respondent / defendant')
        OTHER = 'other', _('Other party')

    litigation = models.ForeignKey(Litigation, on_delete=models.PROTECT, related_name='parties')
    name = models.CharField(max_length=160)
    role = models.CharField(max_length=12, choices=Role)
    added_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('litigation', 'role', 'name'), name='specialist_party_unique'
            )
        ]


class Hearing(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', _('Scheduled')
        HELD = 'held', _('Held')
        ADJOURNED = 'adjourned', _('Adjourned')
        CANCELLED = 'cancelled', _('Cancelled')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    litigation = models.ForeignKey(Litigation, on_delete=models.PROTECT, related_name='hearings')
    replaces = models.OneToOneField(
        'self', on_delete=models.PROTECT, null=True, related_name='replacement'
    )
    milestone = models.OneToOneField(Milestone, on_delete=models.PROTECT)
    starts_at = models.DateTimeField(db_index=True)
    room = models.CharField(max_length=130)
    purpose = models.CharField(max_length=180)
    status = models.CharField(max_length=10, choices=Status, default=Status.SCHEDULED)
    scheduled_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    recorded_by = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, related_name='+')
    recorded_at = models.DateTimeField(null=True)
    disposition = models.CharField(max_length=800, blank=True)

    class Meta:
        ordering = ['starts_at', 'pk']
        indexes = [models.Index(fields=['status', 'starts_at'])]
        constraints = [
            models.CheckConstraint(
                condition=Q(status='scheduled', recorded_by__isnull=True, recorded_at__isnull=True)
                | Q(
                    status__in=('held', 'adjourned', 'cancelled'),
                    recorded_by__isnull=False,
                    recorded_at__isnull=False,
                ),
                name='specialist_hearing_disposition',
            )
        ]


class Proceeding(Recorded):
    class Kind(models.TextChoices):
        FILING = 'filing', _('Filing')
        ORDER = 'order', _('Court order')
        JUDGMENT = 'judgment', _('Judgment')
        APPEAL = 'appeal', _('Appeal')
        PROGRESS = 'progress', _('Progress note')
        STRATEGY = 'strategy', _('Restricted litigation strategy')
        CUSTODY = 'custody', _('Evidence custody')

    litigation = models.ForeignKey(Litigation, on_delete=models.PROTECT, related_name='proceedings')
    kind = models.CharField(max_length=12, choices=Kind)
    occurred_on = models.DateField()
    note = models.CharField(max_length=2200)
    evidence = models.ForeignKey(Document, on_delete=models.PROTECT, null=True, related_name='+')
    recorded_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-occurred_on', '-pk']


class CaseCost(Recorded):
    litigation = models.ForeignKey(Litigation, on_delete=models.PROTECT, related_name='costs')
    incurred_on = models.DateField()
    description = models.CharField(max_length=180)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    currency = models.CharField(max_length=3)
    recorded_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['-incurred_on', '-pk']
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte=0), name='specialist_cost_nonnegative')
        ]


class LitigationOutcome(Recorded):
    class Kind(models.TextChoices):
        JUDGMENT = 'judgment', _('Judgment')
        SETTLEMENT = 'settlement', _('Externally authorised settlement')
        WITHDRAWAL = 'withdrawal', _('Withdrawal / dismissed')
        OTHER = 'other', _('Other documented disposition')

    litigation = models.OneToOneField(Litigation, on_delete=models.PROTECT, related_name='outcome')
    kind = models.CharField(max_length=12, choices=Kind)
    resolved_on = models.DateField()
    disposition = models.CharField(max_length=1200)
    assurance = models.CharField(max_length=10, blank=True)
    evidence = models.ForeignKey(Document, on_delete=models.PROTECT, related_name='+')
    authority = models.ForeignKey(Document, on_delete=models.PROTECT, null=True, related_name='+')
    recorded_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(kind='settlement')
                | (Q(authority__isnull=False) & Q(assurance__in=('totp', 'synthetic'))),
                name='specialist_settlement_authority',
            )
        ]


class LitigationClosure(Recorded):
    litigation = models.OneToOneField(Litigation, on_delete=models.PROTECT, related_name='closure')
    reason = models.CharField(max_length=1200)
    closed_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    closed_at = models.DateTimeField(default=timezone.now)


class Opinion(Dossier):
    pass


class OpinionVersion(Recorded):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opinion = models.ForeignKey(Opinion, on_delete=models.PROTECT, related_name='versions')
    number = models.PositiveIntegerField()
    question = models.CharField(max_length=300)
    facts = models.CharField(max_length=2200)
    authorities = models.CharField(max_length=1800)
    analysis = models.CharField(max_length=3600)
    conclusion = models.CharField(max_length=1200)
    evidence = models.ForeignKey(Document, on_delete=models.PROTECT, null=True, related_name='+')
    authored_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    authored_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-number']
        constraints = [
            models.UniqueConstraint(fields=('opinion', 'number'), name='specialist_opinion_version')
        ]


class SeniorReview(Recorded):
    version = models.OneToOneField(
        OpinionVersion, on_delete=models.PROTECT, related_name='senior_review'
    )
    endorsed = models.BooleanField()
    reason = models.CharField(max_length=1200)
    reviewer = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    reviewed_at = models.DateTimeField(default=timezone.now)


class OpinionIssue(Recorded):
    version = models.OneToOneField(OpinionVersion, on_delete=models.PROTECT, related_name='issue')
    review = models.OneToOneField(ReviewRound, on_delete=models.PROTECT, related_name='opinion_issue')
    issued_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    issued_at = models.DateTimeField(default=timezone.now)
    staff_recipient = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, related_name='+')


class RightsPortfolio(Dossier):
    description = models.CharField(max_length=1000)


class Licence(Recorded):
    class Kind(models.TextChoices):
        MUSIC = 'music', _('Music and audio')
        FILM = 'film', _('Film / television')
        PROGRAMME = 'programme', _('Programme acquisition')
        BROADCAST = 'broadcast', _('Broadcast / retransmission')
        OTT = 'ott', _('Digital / OTT')
        COPYRIGHT = 'copyright', _('Copyright')
        TALENT = 'talent', _('Talent / image / voice')
        ARCHIVE = 'archive', _('Archive / third-party use')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    portfolio = models.ForeignKey(RightsPortfolio, on_delete=models.PROTECT, related_name='licences')
    kind = models.CharField(max_length=12, choices=Kind)
    asset = models.CharField(max_length=180)
    licensor = models.CharField(max_length=160)
    platform = models.CharField(max_length=140)
    territory = models.CharField(max_length=140)
    terms = models.CharField(max_length=1700)
    exclusive = models.BooleanField(default=False)
    starts_on = models.DateField()
    ends_on = models.DateField(db_index=True)
    provenance = models.ForeignKey(Document, on_delete=models.PROTECT, related_name='+')
    execution = models.ForeignKey(Execution, on_delete=models.PROTECT, null=True, related_name='+')
    prior = models.OneToOneField('self', on_delete=models.PROTECT, null=True, related_name='renewal')
    milestone = models.OneToOneField(Milestone, on_delete=models.PROTECT)
    recorded_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['ends_on', 'pk']
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__gte=F('starts_on')), name='specialist_licence_dates'
            )
        ]

    def __str__(self):
        return f'{self.asset} · {self.starts_on:%d/%m/%Y} – {self.ends_on:%d/%m/%Y}'


class RightsIncident(Recorded):
    portfolio = models.ForeignKey(RightsPortfolio, on_delete=models.PROTECT, related_name='incidents')
    licence = models.ForeignKey(Licence, on_delete=models.PROTECT, null=True, related_name='incidents')
    reported_on = models.DateField()
    allegation = models.CharField(max_length=1400)
    immediate_action = models.CharField(max_length=1000)
    referred_matter = models.ForeignKey(Case, on_delete=models.PROTECT, null=True, related_name='+')
    evidence = models.ForeignKey(Document, on_delete=models.PROTECT, null=True, related_name='+')
    recorded_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['-reported_on', '-pk']


class IncidentResolution(Recorded):
    incident = models.OneToOneField(RightsIncident, on_delete=models.PROTECT, related_name='resolution')
    disposition = models.CharField(max_length=1200)
    recorded_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    recorded_at = models.DateTimeField(default=timezone.now)


class Obligation(Dossier):
    requirement = models.CharField(max_length=1700)
    control_area = models.CharField(max_length=100)
    responsible = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    due_on = models.DateField(db_index=True)
    milestone = models.OneToOneField(Milestone, on_delete=models.PROTECT)


class SourceVersion(Recorded):
    obligation = models.ForeignKey(Obligation, on_delete=models.PROTECT, related_name='sources')
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=180)
    citation = models.CharField(max_length=180)
    jurisdiction = models.CharField(max_length=100)
    url = models.URLField(max_length=240)
    effective_on = models.DateField()
    author = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-number']
        constraints = [
            models.UniqueConstraint(fields=('obligation', 'number'), name='specialist_source_version')
        ]


class SourceVerification(Recorded):
    source = models.OneToOneField(SourceVersion, on_delete=models.PROTECT, related_name='verification')
    reason = models.CharField(max_length=1200)
    verified_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    verified_at = models.DateTimeField(default=timezone.now)


class Assessment(Recorded):
    class Result(models.TextChoices):
        DEMONSTRATED = 'demonstrated', _('Control demonstrated')
        GAP = 'gap', _('Gap identified')
        UNVERIFIED = 'unverified', _('Not yet demonstrated')

    obligation = models.ForeignKey(Obligation, on_delete=models.PROTECT, related_name='assessments')
    source = models.ForeignKey(SourceVersion, on_delete=models.PROTECT, related_name='+')
    result = models.CharField(max_length=14, choices=Result)
    findings = models.CharField(max_length=1700)
    evidence = models.ForeignKey(Document, on_delete=models.PROTECT, null=True, related_name='+')
    next_review = models.DateField()
    milestone = models.OneToOneField(Milestone, on_delete=models.PROTECT)
    assessed_at = models.DateTimeField(default=timezone.now)
    assessed_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['-assessed_at', '-pk']


class CorrectiveAction(Recorded):
    assessment = models.OneToOneField(
        Assessment, on_delete=models.PROTECT, related_name='corrective_action'
    )
    responsible = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    plan = models.CharField(max_length=1100)
    milestone = models.OneToOneField(Milestone, on_delete=models.PROTECT)
    due_on = models.DateField(db_index=True)


class Remediation(Recorded):
    action = models.ForeignKey(CorrectiveAction, on_delete=models.PROTECT, related_name='remediations')
    summary = models.CharField(max_length=1200)
    evidence = models.ForeignKey(Document, on_delete=models.PROTECT, related_name='+')
    submitted_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    submitted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-submitted_at', '-pk']


class CorrectiveDecision(Recorded):
    remediation = models.OneToOneField(Remediation, on_delete=models.PROTECT, related_name='decision')
    verified = models.BooleanField()
    notes = models.CharField(max_length=1200)
    verified_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    verified_at = models.DateTimeField(default=timezone.now)


class DeadlineNotice(Recorded):
    class Window(models.TextChoices):
        UPCOMING = 'upcoming', _('Due within seven days')
        TODAY = 'today', _('Due today')
        OVERDUE = 'overdue', _('Overdue for follow-up')

    milestone = models.ForeignKey(Milestone, on_delete=models.PROTECT)
    recipient = models.ForeignKey(USER, on_delete=models.PROTECT, related_name='+')
    notification = models.OneToOneField(
        'identity.Notification', on_delete=models.PROTECT, related_name='deadline_notice'
    )
    date = models.DateField()
    window = models.CharField(max_length=8, choices=Window)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('milestone', 'recipient', 'date', 'window'),
                name='specialist_deadline_delivery_once',
            )
        ]
