"""Named specialist commands; one case lock, revision and audit trail for all modules."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from casework.models import Case, Execution
from casework.policy import assignee, cases_for, clean_documents, current_assignment, head
from casework.services import (
    Conflict,
    StepUpFailed,
    allocate_reference,
    confirm_step_up,
    digest,
    lock_actors,
    lock_case,
    publish_projection,
    record_event,
    transition_case,
)
from casework.storage import read_blob
from identity.models import AuditLog
from identity.policy import Policy
from identity.security import audit, limit_attempt
from matters.models import LegalMatter, Milestone, StaffStatus, Status

from .models import (
    Assessment,
    CaseCost,
    CorrectiveAction,
    CorrectiveDecision,
    Hearing,
    IncidentResolution,
    Licence,
    Litigation,
    LitigationClosure,
    LitigationOutcome,
    Obligation,
    Opinion,
    OpinionVersion,
    Party,
    Proceeding,
    Remediation,
    RightsIncident,
    RightsPortfolio,
    SeniorReview,
    SourceVerification,
    SourceVersion,
)
from .opinions import OPINION_FIELDS, latest_draft
from .policy import (
    MODULES,
    may_operate,
    may_supervise,
    module_for,
    native_departments,
    responsible_people,
    strategy,
)

DOSSIERS = {
    'litigation': Litigation,
    'opinions': Opinion,
    'rights': RightsPortfolio,
    'compliance': Obligation,
}
COMMANDS = {
    'register': gettext_lazy('Register specialist details'),
    'lit_party': gettext_lazy('Add party'),
    'lit_hearing': gettext_lazy('Schedule hearing'),
    'lit_reschedule': gettext_lazy('Adjourn and reschedule'),
    'lit_hearing_result': gettext_lazy('Record hearing outcome'),
    'lit_progress': gettext_lazy('Record proceeding'),
    'lit_cost': gettext_lazy('Record case cost'),
    'lit_outcome': gettext_lazy('Record case outcome'),
    'lit_close': gettext_lazy('Close litigation record'),
    'opinion_version': gettext_lazy('Save new opinion version'),
    'opinion_senior': gettext_lazy('Record Senior review'),
    'rights_licence': gettext_lazy('Record licence'),
    'rights_renew': gettext_lazy('Record renewal'),
    'rights_incident': gettext_lazy('Log rights incident'),
    'rights_resolve': gettext_lazy('Resolve rights incident'),
    'cmp_source': gettext_lazy('Record source version'),
    'cmp_verify': gettext_lazy('Verify source version'),
    'cmp_assess': gettext_lazy('Record assessment'),
    'cmp_remediate': gettext_lazy('Submit corrective evidence'),
    'cmp_verify_action': gettext_lazy('Verify corrective action'),
}
TYPED_COMMANDS = {
    'litigation': (
        'lit_party',
        'lit_hearing',
        'lit_reschedule',
        'lit_hearing_result',
        'lit_progress',
        'lit_cost',
        'lit_outcome',
        'lit_close',
    ),
    'opinions': ('opinion_version', 'opinion_senior'),
    'rights': ('rights_licence', 'rights_renew', 'rights_incident', 'rights_resolve'),
    'compliance': ('cmp_source', 'cmp_verify', 'cmp_assess', 'cmp_remediate', 'cmp_verify_action'),
}


def dossier(case, module):
    return DOSSIERS[module].objects.filter(pk=case.pk).first()


def corporate_contracts(policy, case):
    """Only executed versions in the same department and caller's current legal scope."""
    scope = (
        cases_for(policy).filter(matter__department_id=case.matter.department_id).order_by().values('pk')
    )
    return (
        Execution.objects.filter(version__contract_id__in=scope)
        .select_related('version__contract')
        .order_by('-signed_on')
    )


def litigation_referrals(policy, case):
    return (
        cases_for(policy)
        .filter(
            matter__department_id=case.matter.department_id,
            matter__category_id__in=MODULES['litigation']['categories'],
            litigation__isnull=False,
        )
        .order_by('matter__reference')
    )


def latest_source(record):
    return record.sources.select_related('verification', 'author').first()


def outstanding_actions(record):
    return CorrectiveAction.objects.filter(assessment__obligation=record).exclude(
        remediations__decision__verified=True
    )


def action_allowed(policy, case, module, cmd, record):
    if module not in MODULES or module_for(case) != module or not may_operate(policy, case):
        return False
    if cmd == 'register':
        return record is None and case.matter.status in (Status.ACCEPTED, Status.ASSIGNED, Status.REVIEW)
    if not record or cmd not in TYPED_COMMANDS[module]:
        return False
    supervisor = may_supervise(policy, case)
    if module == 'litigation':
        if hasattr(record, 'closure'):
            return False
        if cmd == 'lit_close':
            return supervisor and hasattr(record, 'outcome')
        if cmd == 'lit_outcome':
            return supervisor and not hasattr(record, 'outcome')
        if cmd in ('lit_hearing', 'lit_reschedule', 'lit_hearing_result', 'lit_party') and hasattr(
            record, 'outcome'
        ):
            return False
        return True
    if module == 'opinions':
        if case.matter.status != Status.REVIEW:
            return False
        if cmd == 'opinion_version':
            return assignee(policy, current_assignment(case)) and policy.user.pk != case.submitted_by_id
        version = latest_draft(case)
        return bool(
            version
            and not hasattr(version, 'senior_review')
            and case.matter.department_id in policy.departments('matter.department')
            and policy.user.pk != version.authored_by_id
            and (case.internal or policy.user.pk != case.submitted_by_id)
        )
    if module == 'rights':
        return cmd != 'rights_resolve' or supervisor
    if cmd == 'cmp_verify':
        source = latest_source(record)
        return bool(
            supervisor
            and source
            and not hasattr(source, 'verification')
            and source.author_id != policy.user.pk
        )
    if cmd == 'cmp_assess':
        source = latest_source(record)
        return bool(source and hasattr(source, 'verification'))
    if cmd == 'cmp_verify_action':
        return supervisor and outstanding_actions(record).exists()
    if cmd == 'cmp_remediate':
        return outstanding_actions(record).exists()
    return True


@transaction.atomic
def open_internal(request, module, data):
    actor, _other = lock_actors(request)
    policy = Policy(actor)
    if module not in MODULES or data['category'].pk not in MODULES[module]['categories']:
        raise ValidationError(_('Choose a category supported by this register.'))
    department = native_departments(policy).filter(pk=data['department'].pk).first()
    if not department:
        raise PermissionDenied
    snapshot = {
        'title': data['title'],
        'summary': data['summary'],
        'category': data['category'].pk,
        'department_id': str(department.pk),
        'priority': data['priority'],
        'required_on': data['required_on'].isoformat(),
        'classification': data['classification'],
    }
    signature = digest(snapshot)
    prior = Case.objects.filter(submitted_by=actor, submission_key=data['submission_key']).first()
    if prior:
        if not prior.internal or prior.submission_digest != signature:
            raise Conflict()
        return prior
    _upcoming(data['required_on'])
    matter = LegalMatter.objects.create(
        reference=allocate_reference('MAT', actor.is_demo),
        title=data['title'],
        internal_summary=data['summary'],
        category=data['category'],
        department=department,
        priority=data['priority'],
        classification=data['classification'],
        is_demo=actor.is_demo,
        status=Status.ACCEPTED,
    )
    case = Case.objects.create(
        matter=matter,
        submitted_by=actor,
        internal=True,
        submission_key=data['submission_key'],
        submission=snapshot
        | {
            'department': department.name,
            'directorate': department.directorate.name,
            'responsible_officer': actor.display_name,
            'submitted_on': timezone.localdate(matter.opened_at).isoformat(),
        },
        submission_digest=signature,
    )
    record_event(request, case, actor, 'open_internal', '')
    return case


def _upcoming(value):
    if value is None or value < timezone.localdate():
        raise ValidationError(_('Choose a date on or after today.'))


def _past(value):
    if value and value > timezone.localdate():
        raise ValidationError(_('The event date cannot be in the future.'))


def _one(qs, value):
    pin = getattr(value, 'pk', value)
    found = qs.filter(pk=pin).first() if pin else None
    if not found:
        raise ValidationError(_('Select a current record from this matter.'))
    return found


def _clean(case, value, *, required=False, integrity=False, repository=True):
    from library.services import specialist_files

    if value:
        result = _one(specialist_files(case) if repository else clean_documents(case), value)
        if integrity:
            read_blob(result)
        return result
    if required:
        raise ValidationError(_('Attach available supporting evidence from this matter.'))
    return None


def _person(case, value):
    return _one(responsible_people(case), value)


def _schedule(case, record, actor, data, previous=None):
    moment = data['starts_at']
    if (
        timezone.is_naive(moment)
        or moment <= timezone.now()
        or (previous and previous.starts_at == moment)
    ):
        raise ValidationError(_('Choose a different future hearing time.'))
    milestone = Milestone.objects.create(
        matter=case.matter, kind=Milestone.Kind.HEARING, date=timezone.localdate(moment)
    )
    return Hearing.objects.create(
        litigation=record,
        starts_at=moment,
        room=data['room'],
        purpose=data['purpose'],
        milestone=milestone,
        scheduled_by=actor,
        replaces=previous,
    )


def _hearing_disposition(hearing, actor, outcome, reason):
    hearing.status, hearing.recorded_by, hearing.recorded_at = outcome, actor, timezone.now()
    hearing.disposition = reason
    hearing.save(update_fields=['status', 'recorded_by', 'recorded_at', 'disposition'])
    Milestone.objects.filter(pk=hearing.milestone_id).update(is_cancelled=True)


def _litigation(request, case, record, actor, policy, cmd, data):
    stage = Hearing.Status
    outcome = LitigationOutcome.Kind
    match cmd:
        case 'lit_party':
            if record.parties.filter(name__iexact=data['name'], role=data['role']).exists():
                raise ValidationError(_('That party is already recorded for this role.'))
            return Party.objects.create(
                litigation=record, name=data['name'], role=data['role'], added_by=actor
            )
        case 'lit_hearing':
            return _schedule(case, record, actor, data)
        case 'lit_reschedule' | 'lit_hearing_result':
            hearing = _one(record.hearings.filter(status=stage.SCHEDULED), data['hearing'])
            if cmd == 'lit_reschedule':
                result = _schedule(case, record, actor, data, previous=hearing)
                _hearing_disposition(hearing, actor, stage.ADJOURNED, data['reason'])
                return result
            resolution = data['hearing_status']
            if resolution not in (stage.HELD, stage.CANCELLED):
                raise ValidationError(_('Select Held or Cancelled. Use reschedule for adjournments.'))
            if resolution == stage.HELD and hearing.starts_at > timezone.now():
                raise ValidationError(_('A future hearing cannot be marked held.'))
            _hearing_disposition(hearing, actor, resolution, data['reason'])
            return hearing
        case 'lit_progress':
            kind = data['kind']
            _past(data['occurred_on'])
            if kind == Proceeding.Kind.STRATEGY:
                if not strategy(policy, case) or data.get('evidence'):
                    # Strategy is restricted text; sensitive files must be sealed in the library.
                    raise PermissionDenied
                proof = None
            else:
                proof = _clean(
                    case,
                    data.get('evidence'),
                    required=kind
                    in (Proceeding.Kind.ORDER, Proceeding.Kind.JUDGMENT, Proceeding.Kind.CUSTODY),
                )
            return Proceeding.objects.create(
                litigation=record,
                kind=kind,
                occurred_on=data['occurred_on'],
                note=data['note'],
                evidence=proof,
                recorded_by=actor,
            )
        case 'lit_cost':
            _past(data['occurred_on'])
            return CaseCost.objects.create(
                litigation=record,
                incurred_on=data['occurred_on'],
                description=data['description'],
                amount=data['amount'],
                currency=data['currency'],
                recorded_by=actor,
            )
        case 'lit_outcome':
            _past(data['occurred_on'])
            if record.hearings.filter(status=stage.SCHEDULED).exists() or not data.get(
                'externally_verified'
            ):
                raise ValidationError(
                    _('Resolve all scheduled hearings and verify the external disposition first.')
                )
            proof = _clean(case, data.get('evidence'), required=True, integrity=True)
            authority = _clean(
                case, data.get('authority'), required=data['kind'] == outcome.SETTLEMENT, integrity=True
            )
            assurance = ''
            if data['kind'] == outcome.SETTLEMENT:
                if not head(policy):
                    raise PermissionDenied
                if authority.pk == proof.pk:
                    raise ValidationError(_('Record separate evidence of settlement authority.'))
                assurance = confirm_step_up(request, actor, data)
            final = LitigationOutcome.objects.create(
                litigation=record,
                kind=data['kind'],
                assurance=assurance,
                resolved_on=data['occurred_on'],
                evidence=proof,
                authority=authority,
                disposition=data['reason'],
                recorded_by=actor,
            )
            transition_case(case, Status.COMPLETED)
            publish_projection(case, StaffStatus.COMPLETED, key='litigation_outcome')
            return final
        case 'lit_close':
            if not data.get('confirmed') or record.hearings.filter(status=stage.SCHEDULED).exists():
                raise ValidationError(_('Confirm appeals, obligations and custody have been addressed.'))
            closure = LitigationClosure.objects.create(
                litigation=record, reason=data['reason'], closed_by=actor
            )
            transition_case(case, Status.CLOSED)
            publish_projection(case, StaffStatus.CLOSED, key='close')
            return closure
    raise PermissionDenied


def _opinions(case, record, actor, cmd, data):
    version = latest_draft(case)
    if cmd == 'opinion_version':
        return OpinionVersion.objects.create(
            opinion=record,
            number=(version.number + 1 if version else 1),
            authored_by=actor,
            evidence=_clean(case, data.get('evidence'), repository=False),
            **{key: data[key] for key in OPINION_FIELDS},
        )
    if not version or version.pk != data['version_id']:
        raise Conflict()
    if not data.get('verified'):
        raise ValidationError(_('Confirm that you reviewed this exact version and its sources.'))
    return SeniorReview.objects.create(
        version=version,
        endorsed=data['senior_outcome'] == 'endorse',
        reason=data['reason'],
        reviewer=actor,
    )


def _rights(case, record, actor, policy, cmd, data):
    if cmd in ('rights_licence', 'rights_renew'):
        prior = (
            _one(record.licences.filter(renewal__isnull=True), data['licence'])
            if cmd == 'rights_renew'
            else None
        )
        if data['ends_on'] < data['starts_on']:
            raise ValidationError(_('Expiry must be on or after the start date.'))
        if prior and (
            data['starts_on'] <= prior.ends_on
            or data['asset'] != prior.asset
            or data['kind'] != prior.kind
        ):
            raise ValidationError(
                _(
                    'A renewal must continue the same asset and rights type after the earlier period ends.'
                )
            )
        if not data.get('provenance_verified'):
            raise ValidationError(_('Verify the licence provenance and scope before registration.'))
        proof = _clean(case, data.get('evidence'), required=True, integrity=True)
        execution = (
            _one(corporate_contracts(policy, case), data['execution']) if data.get('execution') else None
        )
        if execution:
            if execution.signed_copy.scan != 'clean':
                raise ValidationError(_('The linked signed counterpart is not available.'))
            read_blob(execution.signed_copy)
        milestone = Milestone.objects.create(
            matter=case.matter, kind=Milestone.Kind.RIGHTS, date=data['ends_on']
        )
        return Licence.objects.create(
            portfolio=record,
            provenance=proof,
            execution=execution,
            prior=prior,
            milestone=milestone,
            recorded_by=actor,
            **{
                key: data[key]
                for key in (
                    'kind',
                    'asset',
                    'licensor',
                    'platform',
                    'territory',
                    'terms',
                    'exclusive',
                    'starts_on',
                    'ends_on',
                )
            },
        )
    if cmd == 'rights_incident':
        _past(data['occurred_on'])
        licence = _one(record.licences.all(), data['licence']) if data.get('licence') else None
        referral = (
            _one(litigation_referrals(policy, case), data['referred_matter'])
            if data.get('referred_matter')
            else None
        )
        return RightsIncident.objects.create(
            portfolio=record,
            licence=licence,
            referred_matter=referral,
            reported_on=data['occurred_on'],
            allegation=data['allegation'],
            immediate_action=data['immediate_action'],
            evidence=_clean(case, data.get('evidence')),
            recorded_by=actor,
        )
    incident = _one(record.incidents.filter(resolution__isnull=True), data['incident'])
    return IncidentResolution.objects.create(
        incident=incident, disposition=data['reason'], recorded_by=actor
    )


def _cancel_reassessment(record):
    Milestone.objects.filter(pk__in=record.assessments.order_by().values('milestone_id')).update(
        is_cancelled=True
    )


def _compliance(case, record, actor, cmd, data):
    source = latest_source(record)
    match cmd:
        case 'cmp_source':
            URLValidator(schemes=['https'])(data['url'])
            _cancel_reassessment(record)
            # A superseded source invalidates the current display of an earlier assessment, not its history.
            Milestone.objects.filter(pk=record.milestone_id).update(is_cancelled=False)
            return SourceVersion.objects.create(
                obligation=record,
                number=(source.number + 1 if source else 1),
                author=actor,
                **{
                    key: data[key]
                    for key in ('title', 'citation', 'jurisdiction', 'url', 'effective_on')
                },
            )
        case 'cmp_verify':
            if source.pk != data['source_id']:
                raise Conflict()
            if not data.get('verified'):
                raise ValidationError(
                    _('Confirm the source, applicability and effective date independently.')
                )
            return SourceVerification.objects.create(
                source=source, reason=data['reason'], verified_by=actor
            )
        case 'cmp_assess':
            kind = Assessment.Result
            result = data['result']
            if source.pk != data['source_id'] or source.effective_on > timezone.localdate():
                raise ValidationError(_('Use the currently effective verified source.'))
            _upcoming(data['next_review'])
            proof = _clean(
                case, data.get('evidence'), required=result == kind.DEMONSTRATED, integrity=True
            )
            if result == kind.DEMONSTRATED and outstanding_actions(record).exists():
                raise ValidationError(
                    _('Resolve outstanding corrective actions before recording demonstrated control.')
                )
            _cancel_reassessment(record)
            review = Milestone.objects.create(
                matter=case.matter, kind=Milestone.Kind.REASSESS, date=data['next_review']
            )
            assessment = Assessment.objects.create(
                obligation=record,
                source=source,
                result=result,
                milestone=review,
                findings=data['findings'],
                evidence=proof,
                next_review=data['next_review'],
                assessed_by=actor,
            )
            if result == kind.GAP:
                _upcoming(data.get('due_on'))
                if not data.get('plan'):
                    raise ValidationError(_('A gap needs a corrective plan, owner and deadline.'))
                correction = Milestone.objects.create(
                    matter=case.matter, kind=Milestone.Kind.CORRECTIVE, date=data['due_on']
                )
                CorrectiveAction.objects.create(
                    assessment=assessment,
                    responsible=_person(case, data.get('responsible')),
                    milestone=correction,
                    plan=data['plan'],
                    due_on=data['due_on'],
                )
            Milestone.objects.filter(pk=record.milestone_id).update(is_cancelled=True)
            return assessment
        case 'cmp_remediate' | 'cmp_verify_action':
            action = _one(outstanding_actions(record), data['action'])
            current = action.remediations.select_related('decision').first()
            if cmd == 'cmp_remediate':
                if current and not hasattr(current, 'decision'):
                    raise ValidationError(_('The current corrective evidence is awaiting verification.'))
                proof = _clean(case, data.get('evidence'), required=True, integrity=True)
                return Remediation.objects.create(
                    action=action, summary=data['summary'], evidence=proof, submitted_by=actor
                )
            if not current or current.pk != data['remediation_id'] or hasattr(current, 'decision'):
                raise Conflict()
            if actor.pk == current.submitted_by_id:
                raise PermissionDenied
            verified = data['verdict'] == 'verify'
            if verified:
                _clean(case, current.evidence_id, required=True, integrity=True)
                Milestone.objects.filter(pk=action.milestone_id).update(is_cancelled=True)
            return CorrectiveDecision.objects.create(
                remediation=current, verified=verified, notes=data['reason'], verified_by=actor
            )
    raise PermissionDenied


@transaction.atomic
def _run(request, pk, module, cmd, data):
    case, actor, policy, _other = lock_case(request, pk, data['revision'])
    if module not in MODULES:
        raise PermissionDenied
    record = dossier(case, module)
    if not action_allowed(policy, case, module, cmd, record):
        raise PermissionDenied
    before = case.matter.status
    if cmd == 'register':
        attrs = {}
        if module == 'litigation':
            _past(data.get('filed_on'))
            attrs = {
                key: data.get(key)
                for key in ('court', 'external_number', 'filed_on', 'case_type', 'advocate')
            }
        elif module == 'rights':
            attrs = {'description': data['description']}
        elif module == 'compliance':
            milestone = Milestone.objects.create(
                matter=case.matter, kind=Milestone.Kind.COMPLIANCE, date=data['due_on']
            )
            attrs = {
                'requirement': data['requirement'],
                'control_area': data['control_area'],
                'responsible': _person(case, data['responsible']),
                'due_on': data['due_on'],
                'milestone': milestone,
            }
        result = DOSSIERS[module].objects.create(
            case=case,
            reference=allocate_reference(MODULES[module]['code'], actor.is_demo),
            registered_by=actor,
            **attrs,
        )
    elif module == 'litigation':
        result = _litigation(request, case, record, actor, policy, cmd, data)
    elif module == 'opinions':
        result = _opinions(case, record, actor, cmd, data)
    elif module == 'rights':
        result = _rights(case, record, actor, policy, cmd, data)
    else:
        result = _compliance(case, record, actor, cmd, data)
    # No private notes, parties, dates or filenames in the event or audit detail.
    notice = (
        cmd
        if cmd
        in (
            'lit_hearing',
            'lit_reschedule',
            'lit_hearing_result',
            'opinion_senior',
            'rights_licence',
            'rights_renew',
            'cmp_assess',
            'cmp_verify_action',
        )
        else None
    )
    record_event(
        request,
        case,
        actor,
        f'sp_{cmd}',
        before,
        notice=notice,
        extra={'record': str(result.pk), 'module': module},
    )
    return result


def run(request, pk, module, cmd, data):
    if (
        cmd == 'lit_outcome'
        and data.get('kind') == LitigationOutcome.Kind.SETTLEMENT
        and not request.user.is_demo
    ):
        if not limit_attempt(request, str(request.user.pk), purpose='settlement-step-up'):
            raise StepUpFailed(_('Too many confirmation attempts. Try again later.'))
    try:
        return _run(request, pk, module, cmd, data)
    except StepUpFailed:
        audit(request, 'settlement_step_up_failed', area=AuditLog.Area.LEGAL, target=pk)
        raise
