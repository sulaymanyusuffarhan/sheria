"""Named workflow transactions. Private notes are never inputs to a staff projection. Revisions: Git."""

import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from identity.models import AuditLog, User
from identity.policy import Policy
from identity.security import audit, demo_available, limit_attempt, verify_totp
from matters.models import (
    Classification,
    LegalAssignment,
    LegalMatter,
    LegalRequest,
    Milestone,
    RequestUpdate,
    StaffStatus,
    Status,
)

from . import checklist, notices
from .models import (
    Case,
    Checklist,
    Contract,
    ContractVersion,
    Decision,
    Document,
    Event,
    Execution,
    InformationRound,
    Recommendation,
    ReferenceCounter,
    Release,
    ReviewRound,
)
from .policy import (
    ACTIONS,
    CONTRACT_CATEGORIES,
    assignee,
    can_act,
    can_submit,
    cases_for,
    clean_documents,
    current_assignment,
    eligible_officer,
    head,
    matter_documents,
    own_case,
)
from .storage import read_blob, scan_bytes, store


class Conflict(ValidationError):
    def __init__(self):
        super().__init__(_('This request changed. Go back and reload it before trying again.'))


class StepUpFailed(ValidationError):
    pass


PUBLIC_MESSAGES = {
    'litigation_outcome': gettext_noop(
        'Legal Services recorded a case disposition. Contact Legal Services through approved channels for details.'
    ),
    'submit': gettext_noop('Your request has been received by Legal Services.'),
    'accept': gettext_noop('Your request was accepted and will be handled by Legal Services.'),
    'reply': gettext_noop('Your response was received and is awaiting verification.'),
    'verify': gettext_noop('Legal Services verified your response and resumed review.'),
    'approve': gettext_noop(
        'Review is complete. Legal Services will issue the approved response separately.'
    ),
    'reopen': gettext_noop('A new review is in progress. Earlier responses are retained as history.'),
    'close': gettext_noop(
        'Legal Services has closed this request. This does not change the earlier outcome.'
    ),
}


def digest(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    ).hexdigest()


def allocate_reference(namespace, demo):
    year = timezone.localdate().year
    counter, _new = ReferenceCounter.objects.get_or_create(namespace=namespace, year=year)
    ReferenceCounter.objects.filter(pk=counter.pk).update(last_number=F('last_number') + 1)
    counter.refresh_from_db(fields=['last_number'])
    prefix = f'TBC/DLS/{namespace}' if not demo else f'DEMO/DLS/{namespace}'
    return f'{prefix}/{year}/{counter.last_number:05d}'


def lock_actors(request, other_id=None):
    ids = {request.user.pk}
    if other_id:
        ids.add(other_id)
    # Match account-first offboarding and preserve FK key-share availability on PostgreSQL.
    accounts = {
        user.pk: user
        for user in User.objects.select_for_update(no_key=True).filter(pk__in=ids).order_by('pk')
    }
    actor = accounts.get(request.user.pk)
    if not actor or not actor.is_active or actor.auth_epoch != request.user.auth_epoch:
        raise PermissionDenied
    return actor, accounts.get(other_id)


def publish_projection(case, status, *, key=None, text=None):
    if case.internal:
        return
    projection = case.matter.staff_request
    if text is None:
        with translation.override('en'):
            text = _(PUBLIC_MESSAGES[key])
        with translation.override('sw'):
            text_sw = _(PUBLIC_MESSAGES[key])
    else:
        text_sw = ''  # authored correspondence, not an unreviewed translation
    projection.public_status = status
    projection.save(update_fields=['public_status'])
    RequestUpdate.objects.create(request=projection, status=status, message=text, message_sw=text_sw)


def record_event(request, case, actor, command, before, *, reason='', notice=None, extra=None):
    event = Event.objects.create(
        case=case,
        command=command,
        revision=case.revision,
        actor=actor,
        before=before,
        after=case.matter.status,
        reason=reason,
    )
    audit(
        request,
        f'case_{command}',
        area=AuditLog.Area.LEGAL,
        target=case.pk,
        actor=actor,
        detail={'revision': case.revision, 'before': before, 'after': event.after, **(extra or {})},
    )
    if notice:
        notices.notify(event, notice)


@transaction.atomic
def submit(request, data):
    actor, _other = lock_actors(request)
    if not can_submit(Policy(actor)):
        raise PermissionDenied
    dept = actor.department
    is_contract = data['category'].pk in CONTRACT_CATEGORIES
    draft = data.get('draft')
    if is_contract != bool(draft) or not data['declaration']:
        raise ValidationError(
            _('Attach a contract draft for a contract request and confirm the declaration.')
        )
    files = [(Document.Kind.DRAFT, draft)] if draft else []
    if supporting := data.get('attachment'):
        files.append((Document.Kind.SUBMISSION, supporting))
    payload = {
        key: data[key] for key in ('title', 'office', 'contact', 'summary', 'priority', 'declaration')
    } | {
        'required_on': data['required_on'].isoformat(),
        'category': data['category'].pk,
        'contract': {
            key: str(data[key])
            for key in ('counterparty', 'contract_type', 'amount', 'currency', 'duration')
        }
        if is_contract
        else {},
        'files': [{'name': file.name, 'hash': file.checksum, 'kind': kind} for kind, file in files],
    }
    signature = digest(payload)
    prior = Case.objects.filter(submitted_by=actor, submission_key=data['submission_key']).first()
    if prior:
        if prior.submission_digest != signature:
            raise Conflict()
        return prior
    if data['required_on'] < timezone.localdate():
        raise ValidationError(_('The required completion date cannot be in the past.'))
    matter = LegalMatter.objects.create(
        reference=allocate_reference('REQ', actor.is_demo),
        title=data['title'],
        internal_summary=data['summary'],
        category=data['category'],
        department=dept,
        priority=data['priority'],
        is_demo=actor.is_demo,
    )
    LegalRequest.objects.create(
        matter=matter,
        requester=actor,
        title=data['title'],
        summary=data['summary'],
        submitted_at=matter.opened_at,
        due_on=data['required_on'],
    )
    case = Case.objects.create(
        matter=matter,
        submitted_by=actor,
        submission_key=data['submission_key'],
        submission_digest=signature,
        submission=payload
        | {
            'responsible_officer': actor.display_name,
            'department': dept.name,
            'directorate': dept.directorate.name,
            'submitted_on': timezone.localdate(matter.opened_at).isoformat(),
        },
    )
    for kind, file in files:
        store(case, actor, file, kind)
    publish_projection(case, StaffStatus.AWAITING, key='submit')
    record_event(request, case, actor, 'submit', '', notice='submit')
    return case


def lock_case(request, pk, revision, *, staff=False, other_id=None, scope_for=None):
    actor, other = lock_actors(request, other_id)
    policy = Policy(actor)
    scope = Case.objects.filter(submitted_by=actor) if staff else (scope_for or cases_for)(policy)
    if not scope.filter(pk=pk).exists():
        raise Http404
    case = Case.objects.select_for_update(no_key=True).get(pk=pk)
    if staff:
        if not own_case(policy, case):
            raise Http404
    elif not (scope_for or cases_for)(policy).filter(pk=pk).exists():
        raise Http404
    if revision != case.revision:
        raise Conflict()
    if not Case.objects.filter(pk=pk, revision=revision).update(revision=F('revision') + 1):
        raise Conflict()
    case.revision += 1
    return case, actor, policy, other


def latest_package(case):
    return case.reviews.select_related('decision', 'decision__execution', 'version', 'reviewer').first()


def latest_version(case):
    return ContractVersion.objects.filter(contract_id=case.pk).select_related('draft').first()


def active_information(case):
    return case.information_rounds.filter(verified_at__isnull=True).first()


def condition_round(case):
    rounds = case.reviews.order_by('-number')
    clear = rounds.filter(decision__outcome=Decision.Outcome.CLEAR).first()
    if clear:
        rounds = rounds.filter(number__gt=clear.number)
    return (
        rounds.filter(decision__outcome=Decision.Outcome.CONDITIONAL).select_related('decision').first()
    )


def transition_case(case, status):
    case.matter.status = status
    case.matter.save(update_fields=['status'])


def _future(value):
    if value is None or value < timezone.localdate():
        raise ValidationError(_('Choose a date on or after today.'))


def _contract_version(case, actor, draft, data, *, reason):
    if (
        draft.case_id != case.pk
        or draft.kind != Document.Kind.DRAFT
        or draft.scan != Document.Scan.CLEAN
    ):
        raise ValidationError(_('A clean draft document on this request is required.'))
    contract = Contract.objects.filter(pk=case.pk).first()
    if not contract:
        contract = Contract.objects.create(
            case=case, reference=allocate_reference('CTR', actor.is_demo), registered_by=actor
        )
    previous = contract.versions.first()
    version = ContractVersion.objects.create(
        contract=contract,
        number=(previous.number + 1 if previous else 1),
        created_by=actor,
        title=data.get('title') or case.matter.title,
        draft=draft,
        counterparty=data['counterparty'],
        contract_type=data['contract_type'],
        amount=data['amount'],
        currency=data['currency'],
        duration=data['duration'],
        reason=reason,
    )
    Checklist.objects.create(version=version, kind=Checklist.Kind.PRE)
    case.matter.risk = ''
    case.matter.save(update_fields=['risk'])
    return version


@transaction.atomic
def reply(request, pk, data):
    case, actor, _policy, _other = lock_case(request, pk, data['revision'], staff=True)
    round_ = active_information(case)
    if case.matter.status != Status.INFORMATION or not round_ or round_.replied_at:
        raise PermissionDenied
    if draft := data.get('draft'):
        if (
            round_.resume_status != Status.PRELIMINARY
            or case.matter.category_id not in CONTRACT_CATEGORIES
        ):
            raise PermissionDenied
        store(case, actor, draft, Document.Kind.DRAFT)
    if upload := data.get('attachment'):
        store(case, actor, upload, Document.Kind.SUBMISSION)
    round_.reply = data['reply']
    round_.replied_at = timezone.now()
    round_.save(update_fields=['reply', 'replied_at'])
    publish_projection(case, StaffStatus.INFORMATION, key='reply')
    record_event(request, case, actor, 'reply', Status.INFORMATION, notice='reply')


def _evidence(case, ids, *, signed=False):
    # Accept model objects only from validated forms, but reselect clean case-scoped rows under lock.
    ids = {str(getattr(item, 'pk', item)) for item in ids}
    base = matter_documents(case).filter(scan=Document.Scan.CLEAN) if signed else clean_documents(case)
    rows = {str(doc.pk): doc for doc in base.filter(pk__in=ids)}
    if ids != rows.keys():
        raise ValidationError(_('Select only available evidence on this request.'))
    return rows


def _file(doc):
    return {'id': str(doc.pk), 'name': doc.name, 'sha256': doc.checksum, 'kind': doc.kind}


def identification(case, version, assignment):
    info = case.submission
    return {
        key: value
        for key, value in (
            ('title', version.title),
            ('reference', version.contract.reference),
            ('directorate', info['directorate']),
            ('department', info['department']),
            ('responsible_officer', info['responsible_officer']),
            ('counterparty', version.counterparty),
            ('contract_type', version.contract_type),
            ('value', f'{version.amount:.2f} {version.currency}'),
            ('duration', version.duration),
            ('submitted_on', info['submitted_on']),
            ('required_on', info['required_on']),
            ('assigned_to', assignment.officer.display_name if assignment else ''),
        )
    }


def _freeze(request, case, actor, assignment, data):
    if not actor.is_demo and not request.session.get('verified_mfa'):
        raise PermissionDenied
    if not data.get('confirmed') or data['recommendation'] not in Recommendation.values:
        raise ValidationError(_('Confirm a valid recommendation before freezing the review.'))
    version = latest_version(case)
    now = timezone.now()
    number = (case.reviews.values_list('number', flat=True).first() or 0) + 1
    if case.matter.category_id in CONTRACT_CATEGORIES and not version:
        raise ValidationError(_('Register the contract version first.'))
    if matter_documents(case).filter(scan=Document.Scan.PENDING).exists():
        raise ValidationError(_('Wait for documents still in quarantine before sending the review.'))
    all_evidence = {str(doc.pk): doc for doc in clean_documents(case)}
    release_ids = [str(doc.pk) for doc in data.get('deliverables', ())]
    if release_ids and (
        case.internal
        or (
            case.matter.category_id == 'opinion'
            and case.matter.classification != Classification.INTERNAL
        )
    ):
        raise ValidationError(_('These documents are not eligible for staff release.'))
    if any(
        key not in all_evidence or all_evidence[key].kind != Document.Kind.RESPONSE
        for key in release_ids
    ):
        raise ValidationError(_('Only available proposed deliverables can be issued.'))
    prior = condition_round(case)
    conditions = data.get('condition_verification') or ''
    if prior and not conditions:
        raise ValidationError(_('Explain how the earlier Head conditions were verified.'))
    package = {
        'request': case.matter.reference,
        'policy_revision': case.policy_revision,
        'request_revision': case.revision,
        'round': number,
        'reviewer_id': str(actor.pk),
        'reviewer_name': actor.display_name,
        'reviewer_assurance': 'synthetic' if actor.is_demo else 'session-mfa',
        'submitted_at': now.isoformat(),
        'recommendation': data['recommendation'],
        'reason': data['reason'],
        'amendments': data.get('amendments') or '',
        'response': data.get('response') or '',
        'condition_verification': conditions,
        'condition_round': prior.number if prior else None,
        'evidence': [_file(all_evidence[key]) for key in sorted(all_evidence)],
        'deliverables': release_ids,
    }
    if (
        data['recommendation'] == Recommendation.APPROVE
        and not case.internal
        and not package['response']
    ):
        raise ValidationError(
            _('Prepare a staff-facing response for Head review before recommending approval.')
        )
    if (
        data['recommendation'] in (Recommendation.CONDITIONAL, Recommendation.NEGOTIATE)
        and not package['amendments']
    ):
        raise ValidationError(_('Record the proposed amendments or next negotiation steps.'))
    if version:
        if version.number < case.minimum_version:
            raise ValidationError(_('Register the new version before sending this review.'))
        if str(version.draft_id) not in all_evidence:
            raise ValidationError(_('The exact contract draft must be available.'))
        sheet = version.checklists.get(kind=Checklist.Kind.PRE)
        checklist.validate_complete(sheet)
        for value in sheet.answers.values():
            _evidence(case, value.get('evidence', []))
        if data.get('release_draft'):
            package['deliverables'].append(str(version.draft_id))
        package['contract'] = {
            'version_id': str(version.pk),
            'version_number': version.number,
            'draft': _file(version.draft),
            'form': checklist.FORM_REFERENCE,
            'template_revision': sheet.template_revision,
            'identity': identification(case, version, assignment),
            'answers': sheet.answers,
            'risk_class': sheet.risk_class,
            'risks': sheet.risks,
            'sections': [
                {'code': sec.code, 'title': checklist.english(sec.title), 'items': sec.items}
                for sec in checklist.SECTIONS
            ],
        }
    if case.matter.category_id == 'opinion':
        from specialists.opinions import freeze_opinion

        package['opinion'] = freeze_opinion(case, actor, share=data.get('share_opinion', False))
    return ReviewRound.objects.create(
        case=case,
        number=number,
        version=version,
        reviewer=actor,
        submitted_at=now,
        recommendation=data['recommendation'],
        package=package,
        digest=digest(package),
    )


def _verify_package(case, review, data):
    if (
        str(review.pk) != str(data['review_id'])
        or review.digest != data['package_digest']
        or digest(review.package) != review.digest
    ):
        raise Conflict()
    version = latest_version(case)
    if review.version_id != (version.pk if version else None):
        raise Conflict()
    files = review.package['evidence']
    opinion = review.package.get('opinion')
    if opinion:
        from specialists.opinions import check_pin

        check_pin(case, review)
    available = _evidence(case, [entry['id'] for entry in files])
    if any(_file(available[entry['id']]) != entry for entry in files):
        raise Conflict()
    # Reverify both encrypted bytes and digests for the exact draft and proposed disclosures.
    pinned = set(review.package['deliverables'])
    if opinion and opinion['evidence_id']:
        if opinion['evidence_id'] not in available:
            raise Conflict()
        pinned.add(opinion['evidence_id'])
    if version:
        pinned.add(str(version.draft_id))
    for key in pinned:
        read_blob(available[key])
    return available


def confirm_step_up(request, actor, data):
    if actor.is_demo:
        if (
            request.session.get('auth_channel') == 'demo'
            and demo_available()
            and data.get('demo_confirmation')
        ):
            return 'synthetic'
    elif request.session.get('verified_mfa') and verify_totp(actor, data.get('code') or ''):
        return 'totp'
    raise StepUpFailed(
        _('Fresh authentication failed. Wait for a new authenticator code and try again.')
    )


def run(request, pk, command, data):
    step_up = command in ('decide', 'execute')
    if (
        step_up
        and not request.user.is_demo
        and not limit_attempt(request, str(request.user.pk), purpose='legal-step-up')
    ):
        audit(request, 'case_step_up_limited', area=AuditLog.Area.LEGAL, target=pk)
        raise StepUpFailed(_('Too many confirmation attempts. Try again later.'))
    try:
        return _run(request, pk, command, data)
    except StepUpFailed:
        audit(request, 'case_step_up_failed', area=AuditLog.Area.LEGAL, target=pk)
        raise


@transaction.atomic
def _run(request, pk, command, data):
    proposed_id = data['officer'].pk if command == 'assign' else None
    case, actor, policy, proposed = lock_case(request, pk, data['revision'], other_id=proposed_id)
    assignment, review, info = current_assignment(case), latest_package(case), active_information(case)
    if command not in ACTIONS or not can_act(policy, case, command, assignment, review, info):
        raise PermissionDenied
    before = case.matter.status
    reason = data.get('reason') or ''
    extra, notice = {}, None
    match command:
        case 'triage':
            transition_case(case, Status.PRELIMINARY)
        case 'accept':
            if not data.get('verified'):
                raise ValidationError(_('Confirm that the intake evidence is complete.'))
            if matter_documents(case).filter(scan=Document.Scan.PENDING).exists():
                raise ValidationError(_('Pending documents do not satisfy intake verification.'))
            if case.matter.category_id in CONTRACT_CATEGORIES:
                draft = case.documents.filter(kind=Document.Kind.DRAFT).first()
                if not draft:
                    raise ValidationError(_('A clean draft document on this request is required.'))
                version = _contract_version(
                    case, actor, draft, case.submission['contract'], reason=reason
                )
                extra['version'] = str(version.pk)
            transition_case(case, Status.ACCEPTED)
            publish_projection(case, StaffStatus.PROCESSING, key='accept')
            notice = 'accept'
        case 'reject_intake':
            transition_case(case, Status.REJECTED)
            publish_projection(case, StaffStatus.REJECTED, text=data['public_message'])
            notice = 'reject'
        case 'ask':
            _future(data['due_on'])
            InformationRound.objects.create(
                case=case,
                requested_by=actor,
                question=data['public_message'],
                due_on=data['due_on'],
                resume_status=before,
            )
            transition_case(case, Status.INFORMATION)
            publish_projection(case, StaffStatus.INFORMATION, text=data['public_message'])
            notice = 'ask'
        case 'verify':
            if matter_documents(case).filter(scan=Document.Scan.PENDING).exists():
                raise ValidationError(
                    _('Wait for documents still in quarantine before verifying the reply.')
                )
            info.verified_by, info.verified_at = actor, timezone.now()
            info.save(update_fields=['verified_by', 'verified_at'])
            transition_case(case, info.resume_status)
            publish_projection(
                case,
                StaffStatus.AWAITING
                if info.resume_status == Status.PRELIMINARY
                else StaffStatus.PROCESSING,
                key='verify',
            )
            notice = 'verify'
        case 'assign':
            _future(data['due_on'])
            if not proposed or not eligible_officer(case, proposed, actor.pk):
                raise ValidationError(_('Choose an eligible active legal officer for this request.'))
            # End the old assignment without changing its start, owner or due date.
            now = timezone.now()
            LegalAssignment.objects.filter(matter_id=case.pk, revoked_at__isnull=True).update(
                revoked_at=now
            )
            LegalAssignment.objects.create(
                matter=case.matter, officer=proposed, due_on=data['due_on'], starts_at=now
            )
            case.matter.priority = data['priority']
            case.matter.save(update_fields=['priority'])
            transition_case(case, Status.ASSIGNED)
            extra['officer'] = str(proposed.pk)
            notice = 'assign'
        case 'begin':
            transition_case(case, Status.REVIEW)
        case 'note':
            pass  # Only the private event stores note text.
        case 'upload':
            doc = store(case, actor, data['document'], data['kind'])
            if doc.kind not in (Document.Kind.INTERNAL, Document.Kind.RESPONSE):
                raise ValidationError(_('Choose internal evidence or a proposed deliverable.'))
            extra['document'] = str(doc.pk)
        case 'rescan':
            doc = (
                matter_documents(case).filter(pk=data['pending'].pk, scan=Document.Scan.PENDING).first()
            )
            if not doc:
                raise Conflict()
            verdict, method = scan_bytes(read_blob(doc))
            if verdict == Document.Scan.PENDING:
                raise ValidationError(
                    _('The scanner is still unavailable. This document remains quarantined.')
                )
            doc.scan, doc.scan_method = verdict, method
            doc.save(update_fields=['scan', 'scan_method'])
            extra |= {'document': str(doc.pk), 'scan': verdict}
        case 'version':
            doc = store(case, actor, data['draft'], Document.Kind.DRAFT)
            version = _contract_version(case, actor, doc, data, reason=reason)
            extra['version'] = str(version.pk)
        case 'submit_review':
            review = _freeze(request, case, actor, assignment, data)
            transition_case(case, Status.HEAD_REVIEW)
            extra['review'] = str(review.pk)
            notice = command
        case 'decide':
            evidence = _verify_package(case, review, data)
            outcome = data['outcome']
            if not data.get('no_conflict') or outcome not in Decision.Outcome.values:
                raise ValidationError(_('Confirm that you are independent of the request and review.'))
            if outcome == Decision.Outcome.CLEAR and (
                review.recommendation != Recommendation.APPROVE or not data.get('gates_verified')
            ):
                raise ValidationError(
                    _(
                        'Only a legally acceptable recommendation can be cleared after confirming all applicable gates.'
                    )
                )
            gate_doc = data.get('gate_evidence')
            if gate_doc and str(gate_doc.pk) not in evidence:
                raise ValidationError(_('Select gate evidence from this frozen review only.'))
            if (
                outcome == Decision.Outcome.CLEAR
                and review.version_id
                and review.package['contract']['risk_class'] == 'strategic'
                and (not gate_doc or gate_doc.kind != Document.Kind.INTERNAL)
            ):
                raise ValidationError(
                    _('Strategic risk requires documented escalation evidence in the frozen package.')
                )
            if (
                outcome == Decision.Outcome.REJECT
                and not case.internal
                and not data.get('public_message')
            ):
                raise ValidationError(_('Provide a separate staff-facing disposition.'))
            conditional = outcome == Decision.Outcome.CONDITIONAL
            owner = None
            if conditional:
                _future(data.get('condition_due'))
                if (
                    not data.get('conditions')
                    or not assignment
                    or not eligible_officer(case, assignment.officer, actor.pk)
                ):
                    raise ValidationError(
                        _('Conditions require a reason, due date and an eligible assigned owner.')
                    )
                owner = assignment.officer
            assurance = confirm_step_up(request, actor, data)
            Decision.objects.create(
                review=review,
                decided_by=actor,
                decided_name=actor.display_name,
                assurance=assurance,
                outcome=outcome,
                reason=reason,
                condition_owner=owner,
                conditions=(data.get('conditions') or '') if conditional else '',
                condition_due=data.get('condition_due') if conditional else None,
                gate_evidence=gate_doc,
            )
            extra |= {'review': str(review.pk), 'outcome': outcome, 'assurance': assurance}
            notice = outcome
            if outcome == Decision.Outcome.CLEAR:
                transition_case(case, Status.APPROVED)
                publish_projection(case, StaffStatus.PROCESSING, key='approve')
                notice = 'approve'
            elif outcome == Decision.Outcome.REJECT:
                transition_case(case, Status.REJECTED)
                publish_projection(case, StaffStatus.REJECTED, text=data.get('public_message'))
            else:
                transition_case(case, Status.REVIEW if assignment else Status.ACCEPTED)
        case 'issue':
            if not data.get('confirmed'):
                raise ValidationError(_('Confirm release of the exact approved response.'))
            evidence = _verify_package(case, review, data)
            if review.package['deliverables'] and (
                case.internal
                or (
                    case.matter.category_id == 'opinion'
                    and case.matter.classification != Classification.INTERNAL
                )
            ):
                raise ValidationError(_('These documents are not eligible for staff release.'))
            for key in review.package['deliverables']:
                document = evidence[key]
                if document.kind != Document.Kind.RESPONSE and (
                    not review.version_id or document.pk != review.version.draft_id
                ):
                    raise ValidationError(_('The proposed release does not match the approved review.'))
                Release.objects.create(document=document, decision=review.decision, released_by=actor)
            if 'opinion' in review.package:
                from specialists.opinions import issue_opinion

                issue_opinion(case, actor, review)
            transition_case(case, Status.COMPLETED)
            publish_projection(case, StaffStatus.COMPLETED, text=review.package['response'])
            extra['review'] = str(review.pk)
            notice = 'issue'
        case 'reopen':
            case.minimum_version = review.version.number + 1
            case.save(update_fields=['minimum_version'])
            transition_case(case, Status.REVIEW if assignment else Status.ACCEPTED)
            publish_projection(case, StaffStatus.PROCESSING, key='reopen')
            notice = 'reopen'
        case 'execute':
            _verify_package(case, review, data)
            if not data.get('text_matches') or not data.get('gates_verified'):
                raise ValidationError(
                    _('Verify the exact cleared text and the separate execution authority.')
                )
            signed, effective, expires = data['signed_on'], data['effective_on'], data['expires_on']
            if signed > timezone.localdate() or (expires and expires < max(signed, effective)):
                raise ValidationError(_('Check the signing, effective and expiry dates.'))
            signed_doc = store(case, actor, data['document'], Document.Kind.EXECUTION)
            if signed_doc.scan != Document.Scan.CLEAN:
                raise ValidationError(_('Recording execution requires the scanner to be available.'))
            assurance = confirm_step_up(request, actor, data)
            Execution.objects.create(
                version=review.version,
                clearance=review.decision,
                signed_copy=signed_doc,
                signed_on=signed,
                effective_on=effective,
                expires_on=expires,
                signatories=data['signatories'],
                prerequisites=data['prerequisites'],
                recorded_by=actor,
                assurance=assurance,
            )
            Checklist.objects.create(version=review.version, kind=Checklist.Kind.POST)
            if expires:
                Milestone.objects.create(matter=case.matter, kind=Milestone.Kind.EXPIRY, date=expires)
            extra |= {'review': str(review.pk), 'document': str(signed_doc.pk), 'assurance': assurance}
        case 'close':
            if not data.get('retention_confirmed'):
                raise ValidationError(
                    _('Confirm that closure does not authorise deletion or override a legal hold.')
                )
            transition_case(case, Status.CLOSED)
            publish_projection(case, StaffStatus.CLOSED, key='close')
            notice = 'close'
    record_event(request, case, actor, command, before, reason=reason, notice=notice, extra=extra)
    return case


@transaction.atomic
def save_checklist(request, pk, version_pk, section, data):
    case, actor, policy, _other = lock_case(request, pk, data['revision'])
    version, assignment = latest_version(case), current_assignment(case)
    if section == 'XIII':
        version = ContractVersion.objects.filter(contract_id=case.pk, pk=version_pk).first()
    if not version or version.pk != version_pk:
        raise Conflict()
    mine = assignee(policy, assignment)
    if actor.pk == case.submitted_by_id:
        raise PermissionDenied
    if section == 'XIII':
        if not (mine or head(policy)) or not hasattr(version, 'execution'):
            raise PermissionDenied
        sheet = version.checklists.get(kind=Checklist.Kind.POST)
    else:
        if (
            not mine
            or case.matter.status != Status.REVIEW
            or section not in {*[sec.code for sec in checklist.REVIEW_SECTIONS], 'XI'}
        ):
            raise PermissionDenied
        sheet = version.checklists.get(kind=Checklist.Kind.PRE)
    if sheet.finalized_at or sheet.template_revision != checklist.TEMPLATE_REVISION:
        raise Conflict()
    now = timezone.now()
    if section == 'XI':
        risk_class = data.get('risk_class')
        if risk_class not in dict(checklist.RISK_LEVELS):
            raise ValidationError(_('Select a risk classification.'))
        sheet.risk_class, sheet.risks = risk_class, data['risks']
        case.matter.risk = risk_class
        case.matter.save(update_fields=['risk'])
    else:
        allowed = {key for key, _ in checklist.SECTION_MAP[section].items}
        for key, answer in data['answers'].items():
            if key not in allowed:
                raise ValidationError(_('Select an item from this section.'))
            if answer['choice'] and answer['choice'] not in dict(checklist.CHOICES):
                raise ValidationError(_('Choose Yes, No, N/A or Unanswered.'))
            if answer['choice']:
                sheet.answers[key] = {
                    'choice': answer['choice'],
                    'remarks': answer['remarks'],
                    'evidence': sorted(_evidence(case, answer['evidence'], signed=section == 'XIII')),
                    'by': actor.display_name,
                    'actor_id': str(actor.pk),
                    'at': now.isoformat(),
                }
            else:
                sheet.answers.pop(key, None)
        if section == 'XIII':
            sheet.post_details = data['post_details']
            if data['finalize']:
                if not all(
                    checklist.valid_answer(sheet.answers.get(key, {})) for key in checklist.POST_IDS
                ) or not all(sheet.post_details.values()):
                    raise ValidationError(
                        _(
                            'Complete all six checks and the manager, monitoring and archive fields before finalising.'
                        )
                    )
                sheet.finalized_at, sheet.finalized_by = now, actor
    sheet.save()
    record_event(
        request,
        case,
        actor,
        'save_checklist',
        case.matter.status,
        extra={'version': str(version.pk), 'section': section, 'finalized': bool(sheet.finalized_at)},
    )
    return case
