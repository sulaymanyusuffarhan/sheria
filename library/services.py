"""Versioned repository, custody commands and audience checks on the shared case lock."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from casework.models import Case, Document
from casework.policy import cases_for, clean_documents, head
from casework.services import allocate_reference, lock_case, record_event
from casework.storage import read_blob, scan_bytes, store
from matters.selectors import active_assignments, records_scope
from specialists.policy import may_operate

from .models import Custody, Item, Version

COMMANDS = {
    'version': gettext_lazy('Upload new version'),
    'rescan': gettext_lazy('Recheck quarantined version'),
    'hold': gettext_lazy('Place legal hold'),
    'lift': gettext_lazy('Release legal hold'),
    'archive': gettext_lazy('Archive record'),
}


def items_for_legal(policy):
    scope = cases_for(policy).order_by().values('pk')
    records = Item.objects.filter(case_id__in=scope)
    if head(policy):
        return records
    mine = (
        active_assignments(policy.user.is_demo)
        .filter(officer=policy.user)
        .order_by()
        .values('matter_id')
    )
    allowed = Q(sealed=False)
    if policy.has('matter.assigned'):
        allowed |= Q(case_id__in=mine)
    return records.filter(allowed)


def items_for_records(policy):
    return Item.objects.filter(sealed=False, case_id__in=records_scope(policy).order_by().values('pk'))


def records_cases(policy):
    return Case.objects.filter(pk__in=records_scope(policy).order_by().values('pk'))


def matter_files(policy):
    return Document.objects.filter(case_id__in=cases_for(policy).order_by().values('pk')).exclude(
        kind=Document.Kind.REPOSITORY
    )


def specialist_files(case):
    """Unsealed clean versions can be pinned as same-matter specialist evidence."""
    return clean_documents(case) | Document.objects.filter(
        case=case,
        kind=Document.Kind.REPOSITORY,
        scan=Document.Scan.CLEAN,
        library_version__item__case=case,
        library_version__item__sealed=False,
    )


def allowed(policy, item, command):
    legal = items_for_legal(policy).filter(pk=item.pk).exists()
    records = items_for_records(policy).filter(pk=item.pk).exists()
    if command == 'version':
        return legal and not item.archived_at and may_operate(policy, item.case)
    if command == 'rescan':
        return (
            legal
            and may_operate(policy, item.case)
            and item.versions.filter(document__scan=Document.Scan.PENDING).exists()
        )
    if command == 'lift':
        return legal and head(policy) and item.hold
    if command == 'hold':
        return not item.hold and (records or (legal and head(policy)))
    if command == 'archive':
        return not item.hold and not item.archived_at and (records or (legal and head(policy)))
    return False


def _put_version(item, case, actor, data):
    version = item.versions.values_list('number', flat=True).first() or 0
    document = store(case, actor, data['document'], Document.Kind.REPOSITORY)
    return Version.objects.create(
        item=item, document=document, number=version + 1, provenance=data['provenance']
    )


@transaction.atomic
def create(request, pk, data):
    case, actor, policy, _other = lock_case(request, pk, data['revision'])
    if not may_operate(policy, case):
        raise PermissionDenied
    if data.get('sealed', False) and not (
        head(policy)
        or policy.has('matter.assigned')
        and active_assignments(actor.is_demo).filter(matter_id=case.pk, officer=actor).exists()
    ):
        raise PermissionDenied
    item = Item.objects.create(
        case=case,
        reference=allocate_reference('DOC', actor.is_demo),
        title=data['title'],
        category=data['category'],
        tags=data['tags'],
        sealed=data.get('sealed', False),
        retain_until=data.get('retain_until'),
        created_by=actor,
    )
    version = _put_version(item, case, actor, data)
    record_event(
        request,
        case,
        actor,
        'library_register',
        case.matter.status,
        extra={'item': str(item.pk), 'version': str(version.pk)},
    )
    return item


@transaction.atomic
def execute(request, pk, command, data):
    readers = items_for_legal(request.policy)
    metadata = items_for_records(request.policy)
    if not (item := readers.filter(pk=pk).first() or metadata.filter(pk=pk).first()):
        raise Http404
    case, actor, policy, _other = lock_case(
        request,
        item.case_id,
        data['revision'],
        scope_for=(cases_for if readers.filter(pk=pk).exists() else records_cases),
    )
    current = Item.objects.select_for_update(no_key=True).get(pk=item.pk, case=case)
    if not allowed(policy, current, command):
        raise PermissionDenied
    extra = {'item': str(current.pk)}
    if command == 'version':
        extra['version'] = str(_put_version(current, case, actor, data).pk)
    elif command == 'rescan':
        pending = (
            current.versions.filter(pk=data['pending'].pk, document__scan=Document.Scan.PENDING)
            .select_related('document')
            .first()
        )
        if not pending:
            raise ValidationError(_('Select a quarantined version on this record.'))
        doc = pending.document
        scan, method = scan_bytes(read_blob(doc))
        if scan == Document.Scan.PENDING:
            raise ValidationError(
                _('The scanner is still unavailable. This document remains quarantined.')
            )
        doc.scan, doc.scan_method = scan, method
        doc.save(update_fields=['scan', 'scan_method'])
        extra |= {'version': str(pending.pk), 'scan': scan}
    elif command in Custody.Action.values:
        if command == 'archive':
            if current.versions.filter(document__scan=Document.Scan.PENDING).exists():
                raise ValidationError(_('Resolve quarantined versions before archiving this record.'))
            current.archived_at = timezone.now()
            current.save(update_fields=['archived_at'])
        else:
            current.hold = command == 'hold'
            current.save(update_fields=['hold'])
        Custody.objects.create(item=current, action=command, reason=data['reason'], actor=actor)
    else:
        raise PermissionDenied
    # A generic case event never discloses sealed metadata, free text, blob IDs or filenames.
    record_event(request, case, actor, f'library_{command}', case.matter.status, extra=extra)
    return current
