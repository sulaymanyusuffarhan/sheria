"""Central access policy for Phase 1. Introduced: 2026-11-18; revisions: Git."""

from django.db.models import Q
from django.utils import timezone

from .models import RoleCode, RoleGrant, Scope, User

ROLE_SPECS = {
    RoleCode.ICT: (
        Scope.INSTITUTION,
        ('identity.manage', 'organisation.manage', 'access.apply', 'audit.technical'),
    ),
    RoleCode.STAFF: (Scope.SELF, ('request.own',)),
    RoleCode.HOD: (Scope.DEPARTMENT, ('request.department',)),
    RoleCode.OFFICER: (Scope.ASSIGNED, ('matter.assigned',)),
    RoleCode.SENIOR: (Scope.DEPARTMENT, ('matter.department',)),
    RoleCode.HEAD: (Scope.INSTITUTION, ('matter.institution', 'access.decide')),
    RoleCode.MANAGEMENT: (Scope.INSTITUTION, ('reports.aggregate',)),
    RoleCode.AUDITOR: (Scope.INSTITUTION, ('audit.read',)),
    RoleCode.RECORDS: (Scope.DEPARTMENT, ('records.department',)),
}
DELEGABLE_ROLES = (RoleCode.HOD, RoleCode.OFFICER, RoleCode.SENIOR, RoleCode.RECORDS)
ROLE_ORDER = (
    RoleCode.ICT,
    RoleCode.HEAD,
    RoleCode.SENIOR,
    RoleCode.OFFICER,
    RoleCode.MANAGEMENT,
    RoleCode.AUDITOR,
    RoleCode.RECORDS,
    RoleCode.HOD,
    RoleCode.STAFF,
)
LEGAL_CAPABILITIES = ('matter.institution', 'matter.department', 'matter.assigned')
STAFF_CAPABILITIES = ('request.own', 'request.department')


class Policy:
    def __init__(self, user):
        self.user = user
        self.grants = []
        self._capabilities = {}
        if not user.is_authenticated or not user.is_active:
            return
        now = timezone.now()
        candidates = (
            RoleGrant.objects.filter(user=user, granted_at__lte=now, revoked_at__isnull=True)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .select_related('role', 'department', 'department__directorate')
            .prefetch_related('role__permissions')
        )
        for grant in candidates:
            code = grant.role_id
            spec = ROLE_SPECS.get(code)
            if not spec or spec[0] != grant.role.scope:
                continue
            if (code == RoleCode.ICT) != (user.kind == User.Kind.TECHNICAL):
                continue
            if grant.role.scope == Scope.DEPARTMENT:
                dept = grant.department
                if (
                    not dept
                    or not dept.is_active
                    or not dept.directorate.is_active
                    or dept.is_demo != user.is_demo
                ):
                    continue
                if code == RoleCode.HOD and grant.department_id != user.department_id:
                    continue
            elif grant.department_id:
                continue
            self.grants.append(grant)
            granted = {permission.code for permission in grant.role.permissions.all()}
            for cap in granted.intersection(spec[1]):
                self._capabilities.setdefault(cap, []).append(grant)

    def has(self, capability):
        return bool(self._capabilities.get(capability))

    def any(self, capabilities):
        return any(self.has(capability) for capability in capabilities)

    def departments(self, capability):
        return [
            grant.department_id
            for grant in self._capabilities.get(capability, ())
            if grant.department_id
        ]

    @property
    def primary_role(self):
        roles = {grant.role_id for grant in self.grants}
        return next((role for role in ROLE_ORDER if role in roles), '')

    @property
    def requires_mfa(self):
        return self.user.kind == User.Kind.TECHNICAL or any(
            grant.role_id != RoleCode.STAFF for grant in self.grants
        )

    @property
    def landing(self):
        if self.any(LEGAL_CAPABILITIES):
            return 'legal_dashboard'
        if self.has('identity.manage'):
            return 'ict_dashboard'
        if self.has('reports.aggregate'):
            return 'management_dashboard'
        if self.has('audit.read'):
            return 'audit_log'
        if self.has('records.department'):
            return 'records_register'
        if self.any(STAFF_CAPABILITIES):
            return 'staff_dashboard'
        return 'profile'
