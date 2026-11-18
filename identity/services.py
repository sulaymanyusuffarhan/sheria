"""Transactional identity administration. Introduced: 2026-11-18; revisions: Git."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from matters.models import LegalAssignment, MatterAccess

from .models import AccessRequest, Department, Directorate, Role, RoleCode, RoleGrant, Scope, User
from .policy import DELEGABLE_ROLES, Policy
from .security import audit

OPEN_REQUESTS = (AccessRequest.Status.PENDING, AccessRequest.Status.APPROVED)


def require(request, capability):
    if not request.policy.has(capability):
        raise PermissionDenied


def _editable_user(actor, target):
    if target.pk == actor.pk or target.kind != User.Kind.EMPLOYEE or target.is_demo != actor.is_demo:
        raise PermissionDenied


def _lock_active_department(dept, demo):
    parent = Directorate.objects.select_for_update().get(pk=dept.directorate_id)
    unit = Department.objects.select_for_update().get(pk=dept.pk)
    if not parent.is_active or not unit.is_active or unit.is_demo != demo or parent.is_demo != demo:
        raise ValidationError(_('Select an active department.'))


def _revoke_matter_relationships(target, now):
    LegalAssignment.objects.filter(officer=target, revoked_at__isnull=True).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gt=now)
    ).update(revoked_at=now)
    MatterAccess.objects.filter(user=target, expires_at__gt=now, revoked_at__isnull=True).update(
        revoked_at=now
    )


def _validate_scope(target, role, dept):
    if role.code not in DELEGABLE_ROLES or not target.is_active:
        raise ValidationError(_('That role or account is not eligible.'))
    if role.scope == Scope.DEPARTMENT:
        if not dept:
            raise ValidationError(_('Select an active department for this role.'))
        _lock_active_department(dept, target.is_demo)
        if role.code == RoleCode.HOD and target.department_id != dept.pk:
            raise ValidationError(_('A department head must belong to the selected department.'))
    elif dept:
        raise ValidationError(_('Leave the department blank for this role.'))


def invalidate_access(target, *, except_request=None):
    """Invalidate active sessions and stale requests while holding the account lock."""
    target.auth_epoch += 1
    target.save(update_fields=['auth_epoch'])
    AccessRequest.objects.filter(target=target, status__in=OPEN_REQUESTS).exclude(
        pk=except_request
    ).update(status=AccessRequest.Status.CANCELLED)


@transaction.atomic
def create_employee(request, data):
    require(request, 'identity.manage')
    _lock_active_department(data['department'], request.user.is_demo)
    user = User(**data, kind=User.Kind.EMPLOYEE, is_demo=request.user.is_demo)
    user.set_unusable_password()
    user.save()
    RoleGrant.objects.create(user=user, role=Role.objects.get(pk=RoleCode.STAFF))
    audit(request, 'employee_created', target=user.pk, detail={'department': str(user.department_id)})
    return user


@transaction.atomic
def edit_employee(request, account, data):
    require(request, 'identity.manage')
    target = User.objects.select_for_update().get(pk=account.pk)
    _editable_user(request.user, target)
    _lock_active_department(data['department'], target.is_demo)
    old = {'active': target.is_active, 'department': str(target.department_id or '')}
    for field, value in data.items():
        setattr(target, field, value)
    target.save(update_fields=list(data))
    new = {'active': target.is_active, 'department': str(target.department_id or '')}
    if old != new:
        invalidate_access(target)
        now = timezone.now()
        RoleGrant.objects.filter(user=target, revoked_at__isnull=True).exclude(
            role_id=RoleCode.STAFF
        ).update(revoked_at=now)
        _revoke_matter_relationships(target, now)
    audit(request, 'employee_updated', target=target.pk, detail={'before': old, 'after': new})
    return target


@transaction.atomic
def request_access(request, target, role, department, reason):
    require(request, 'access.apply')
    target = User.objects.select_for_update().get(pk=target.pk)
    _editable_user(request.user, target)
    _validate_scope(target, role, department)
    if any(
        grant.role_id == role.pk and grant.department_id == (department.pk if department else None)
        for grant in Policy(target).grants
    ):
        raise ValidationError(_('This account already has that scoped role.'))
    if AccessRequest.objects.filter(
        target=target, role=role, department=department, status__in=OPEN_REQUESTS
    ).exists():
        raise ValidationError(_('There is already an open request for this role and scope.'))
    item = AccessRequest.objects.create(
        target=target,
        role=role,
        department=department,
        reason=reason,
        requested_by=request.user,
        target_epoch=target.auth_epoch,
    )
    audit(request, 'access_requested', target=item.pk, detail={'role': role.code})
    return item


def _locked_access(request, item):
    # Match the account-first lock order used by edit/offboarding.
    target = User.objects.select_for_update().get(pk=item.target_id)
    _editable_user(request.user, target)
    record = AccessRequest.objects.select_for_update().get(pk=item.pk)
    if target.auth_epoch != record.target_epoch:
        raise ValidationError(_('The account changed. Open a new access request.'))
    return target, record


@transaction.atomic
def decide_access(request, item, decision, reason):
    require(request, 'access.decide')
    target, item = _locked_access(request, item)
    if item.requested_by_id == request.user.pk or item.target_id == request.user.pk:
        raise PermissionDenied
    if item.status != AccessRequest.Status.PENDING:
        raise ValidationError(_('This request is no longer awaiting a decision.'))
    _validate_scope(target, item.role, item.department)
    if decision not in (AccessRequest.Status.APPROVED, AccessRequest.Status.REJECTED):
        raise ValidationError(_('Choose a valid access decision.'))
    item.status = decision
    item.decision_reason = reason
    item.decided_by = request.user
    item.decided_at = timezone.now()
    item.save(update_fields=['status', 'decision_reason', 'decided_by', 'decided_at'])
    audit(
        request,
        'access_decided',
        target=item.pk,
        detail={'before': AccessRequest.Status.PENDING, 'after': decision, 'role': item.role_id},
    )


@transaction.atomic
def apply_access(request, item):
    require(request, 'access.apply')
    target, item = _locked_access(request, item)
    if item.status != AccessRequest.Status.APPROVED or not item.decided_by_id:
        raise ValidationError(_('Only approved requests can be applied.'))
    if item.decided_by_id in (request.user.pk, item.requested_by_id, target.pk):
        raise PermissionDenied
    # An approval from a disabled or no longer authorised approver is not actionable.
    if not Policy(item.decided_by).has('access.decide'):
        raise ValidationError(_('The approving officer no longer has decision authority.'))
    _validate_scope(target, item.role, item.department)
    RoleGrant.objects.update_or_create(
        user=target,
        role=item.role,
        scope_key=str(item.department_id or item.role.scope),
        defaults={
            'department': item.department,
            'revoked_at': None,
            'expires_at': None,
            'granted_at': timezone.now(),
        },
    )
    item.status = AccessRequest.Status.APPLIED
    item.applied_by = request.user
    item.applied_at = timezone.now()
    item.save(update_fields=['status', 'applied_by', 'applied_at'])
    invalidate_access(target, except_request=item.pk)
    audit(
        request,
        'access_applied',
        target=item.pk,
        detail={
            'before': AccessRequest.Status.APPROVED,
            'after': AccessRequest.Status.APPLIED,
            'role': item.role_id,
        },
    )


@transaction.atomic
def revoke_access(request, grant):
    require(request, 'access.apply')
    target = User.objects.select_for_update().get(pk=grant.user_id)
    _editable_user(request.user, target)
    grant = RoleGrant.objects.select_for_update().get(pk=grant.pk)
    if grant.role_id not in DELEGABLE_ROLES or grant.revoked_at:
        raise ValidationError(_('This grant cannot be revoked here.'))
    grant.revoked_at = timezone.now()
    grant.save(update_fields=['revoked_at'])
    if grant.role_id == RoleCode.OFFICER:
        _revoke_matter_relationships(target, grant.revoked_at)
    invalidate_access(target)
    audit(
        request,
        'access_revoked',
        target=target.pk,
        detail={'role': grant.role_id, 'scope': grant.scope_key},
    )
