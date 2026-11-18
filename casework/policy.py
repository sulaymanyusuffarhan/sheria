"""Current-state action and evidence boundaries, shared by views and services. Revisions: Git."""

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from identity.models import RoleCode, User
from identity.policy import Policy
from matters.models import Classification, MatterAccess, Status
from matters.selectors import active_assignments, legal_scope, staff_scope

from .models import Case, Decision, Document

CONTRACT_CATEGORIES = ('contract', 'mou')

ACTIONS = {
    'triage': (_('Start preliminary review'), 'search-file'),
    'accept': (_('Accept request'), 'check-circle'),
    'reject_intake': (_('Reject at intake'), 'x-circle'),
    'ask': (_('Request additional information'), 'message'),
    'verify': (_('Verify staff response'), 'check-shield'),
    'assign': (_('Assign legal officer'), 'user'),
    'begin': (_('Begin legal review'), 'briefcase'),
    'note': (_('Add internal note'), 'message'),
    'upload': (_('Upload legal document'), 'file'),
    'rescan': (_('Recheck quarantined document'), 'shield'),
    'version': (_('Register new contract version'), 'files'),
    'submit_review': (_('Send to Head of Legal Services'), 'arrow-right'),
    'decide': (_('Record Head decision'), 'check-shield'),
    'issue': (_('Issue approved response'), 'arrow-right'),
    'reopen': (_('Open a new contract review'), 'files'),
    'execute': (_('Record external execution'), 'file'),
    'close': (_('Close request'), 'lock'),
}


def cases_for(policy):
    return Case.objects.filter(matter_id__in=legal_scope(policy).order_by().values('pk')).select_related(
        'matter__category', 'matter__department__directorate'
    )


def can_submit(policy):
    if not policy.has('request.own') or policy.user.kind != User.Kind.EMPLOYEE:
        return False
    dept = policy.user.department
    return bool(
        dept
        and dept.is_active
        and dept.directorate.is_active
        and dept.is_demo == policy.user.is_demo
        and dept.directorate.is_demo == policy.user.is_demo
    )


def own_case(policy, case):
    return case.submitted_by_id == policy.user.pk and staff_scope(policy).filter(pk=case.pk).exists()


def head(policy):
    return policy.has('matter.institution')


def supervises(policy, case):
    return head(policy) or case.matter.department_id in policy.departments('matter.department')


def current_assignment(case):
    return (
        active_assignments(case.matter.is_demo)
        .filter(matter_id=case.pk)
        .select_related('officer')
        .first()
    )


def assignee(policy, assignment):
    return bool(assignment and assignment.officer_id == policy.user.pk and policy.has('matter.assigned'))


def has_named_clearance(case, user):
    return (
        case.matter.classification == Classification.INTERNAL
        or MatterAccess.objects.filter(
            matter_id=case.pk,
            user=user,
            classification=case.matter.classification,
            expires_at__gt=timezone.now(),
            revoked_at__isnull=True,
        ).exists()
    )


def eligible_officer(case, user, assigner_id):
    return (
        user.is_active
        and user.is_demo == case.matter.is_demo
        and user.pk not in (case.submitted_by_id, assigner_id)
        and Policy(user).has('matter.assigned')
        and has_named_clearance(case, user)
    )


def officer_choices(case, policy):
    members = User.objects.filter(
        is_active=True,
        is_demo=case.matter.is_demo,
        role_grants__role_id=RoleCode.OFFICER,
        role_grants__revoked_at__isnull=True,
    ).distinct()
    ids = [user.pk for user in members if eligible_officer(case, user, policy.user.pk)]
    return User.objects.filter(pk__in=ids).order_by('first_name', 'last_name', 'username')


def can_act(policy, case, cmd, assignment, latest_review=None, information=None):
    # The caller must also pass the legal read scope (including current named clearance).
    stage = case.matter.status
    mine = assignee(policy, assignment)
    owner = policy.user.pk == case.submitted_by_id
    supervisor = supervises(policy, case) and (case.internal or not owner)
    lead = head(policy) and not owner
    is_contract = case.matter.category_id in CONTRACT_CATEGORIES
    reviewed = getattr(latest_review, 'decision', None)
    clear = bool(reviewed and reviewed.outcome == Decision.Outcome.CLEAR)
    match cmd:
        case 'triage':
            return supervisor and stage == Status.SUBMITTED
        case 'accept' | 'reject_intake':
            return supervisor and stage == Status.PRELIMINARY
        case 'ask':
            if case.internal:
                return False
            return (supervisor and stage == Status.PRELIMINARY) or (
                mine and not owner and stage == Status.REVIEW
            )
        case 'verify':
            return (
                stage == Status.INFORMATION
                and information
                and information.replied_at
                and not information.verified_at
                and (
                    supervisor
                    if information.resume_status == Status.PRELIMINARY
                    else (mine or supervisor) and not owner
                )
            )
        case 'assign':
            return supervisor and stage in (Status.ACCEPTED, Status.ASSIGNED, Status.REVIEW)
        case 'begin':
            return mine and not owner and stage == Status.ASSIGNED
        case 'note':
            return (
                (mine or supervisor)
                and not owner
                and stage in (Status.PRELIMINARY, Status.ASSIGNED, Status.REVIEW)
            )
        case 'rescan':
            return (
                (mine or supervisor)
                and not owner
                and stage
                in (
                    Status.SUBMITTED,
                    Status.PRELIMINARY,
                    Status.INFORMATION,
                    Status.ACCEPTED,
                    Status.ASSIGNED,
                    Status.REVIEW,
                )
                and matter_documents(case).filter(scan=Document.Scan.PENDING).exists()
            )
        case 'upload':
            return mine and not owner and stage == Status.REVIEW
        case 'submit_review':
            if case.matter.category_id in ('litigation', 'defamation'):
                return False
            return (
                mine
                and not owner
                and stage == Status.REVIEW
                and (not case.internal or case.matter.category_id == 'opinion')
            )
        case 'version':
            return is_contract and mine and not owner and stage == Status.REVIEW
        case 'decide':
            return (
                lead
                and stage == Status.HEAD_REVIEW
                and latest_review
                and not reviewed
                and latest_review.reviewer_id != policy.user.pk
                and str(policy.user.pk)
                not in (
                    latest_review.package.get('opinion', {}).get('author_id'),
                    latest_review.package.get('opinion', {}).get('senior_id'),
                )
            )
        case 'issue':
            return lead and clear and stage == Status.APPROVED
        case 'reopen':
            return lead and is_contract and clear and stage in (Status.APPROVED, Status.COMPLETED)
        case 'execute':
            return (
                lead
                and is_contract
                and clear
                and stage in (Status.APPROVED, Status.COMPLETED, Status.CLOSED)
                and not hasattr(reviewed, 'execution')
            )
        case 'close':
            if case.matter.category_id in ('litigation', 'defamation') and hasattr(case, 'litigation'):
                return False
            return supervisor and stage in (Status.COMPLETED, Status.REJECTED)
    return False


def matter_documents(case):
    return case.documents.exclude(kind=Document.Kind.REPOSITORY)


def clean_documents(case):
    return matter_documents(case).filter(scan=Document.Scan.CLEAN).exclude(kind=Document.Kind.EXECUTION)


def staff_documents(policy, case):
    if not own_case(policy, case):
        return Document.objects.none()
    return (
        matter_documents(case)
        .filter(scan=Document.Scan.CLEAN)
        .filter(
            Q(uploaded_by=policy.user, kind__in=(Document.Kind.SUBMISSION, Document.Kind.DRAFT))
            | (
                Q(releases__decision__review__case=case)
                if case.matter.category_id != 'opinion'
                or case.matter.classification == Classification.INTERNAL
                else Q(pk__isnull=True)
            )
        )
        .distinct()
    )
