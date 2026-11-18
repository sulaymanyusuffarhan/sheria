"""Indexed logical files. Bytes are stored only in the shared casework document store."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from casework.models import Case, Document, Recorded


class Item(models.Model):
    class Category(models.TextChoices):
        CONTRACT = 'contract', _('Contracts / agreements')
        COURT = 'court', _('Court filings / judgments')
        OPINION = 'opinion', _('Legal opinions')
        CORRESPONDENCE = 'correspondence', _('Correspondence')
        MEMORANDUM = 'memorandum', _('Internal memoranda')
        RIGHTS = 'rights', _('Intellectual property / rights')
        REGULATORY = 'regulatory', _('Regulatory / compliance')
        TEMPLATE = 'template', _('Templates / research')
        OTHER = 'other', _('Other legal documents')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name='library_items')
    reference = models.CharField(max_length=36, unique=True)
    title = models.CharField(max_length=160)
    category = models.CharField(max_length=16, choices=Category)
    tags = models.CharField(max_length=160, blank=True)
    sealed = models.BooleanField(default=False)
    retain_until = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    archived_at = models.DateTimeField(null=True)
    hold = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['case', 'category', 'archived_at'])]


class Version(Recorded):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='versions')
    document = models.OneToOneField(Document, on_delete=models.PROTECT, related_name='library_version')
    number = models.PositiveIntegerField()
    provenance = models.CharField(max_length=600)

    class Meta:
        ordering = ['-number']
        constraints = [models.UniqueConstraint(fields=['item', 'number'], name='library_version_unique')]


class Custody(Recorded):
    class Action(models.TextChoices):
        ARCHIVE = 'archive', _('Archive')
        HOLD = 'hold', _('Place hold')
        LIFT = 'lift', _('Release hold')

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='custody')
    action = models.CharField(max_length=10, choices=Action)
    reason = models.CharField(max_length=900)
    recorded_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['-pk']
