"""Specialist actions use the central matter scope and active relationships."""

from django.utils.translation import gettext_lazy as _

from casework.policy import assignee, current_assignment, head, supervises
from identity.models import Department, User
from matters.models import Status

MODULES = {
    'litigation': {
        'title': _('Litigation register'),
        'label': _('Litigation'),
        'icon': 'gavel',
        'categories': ('litigation', 'defamation'),
        'code': 'LIT',
    },
    'opinions': {
        'title': _('Legal opinions'),
        'label': _('Opinion'),
        'icon': 'file',
        'categories': ('opinion',),
        'code': 'OPN',
    },
    'rights': {
        'title': _('IP & broadcasting rights'),
        'label': _('Rights portfolio'),
        'icon': 'flag',
        'categories': ('ip', 'broadcasting', 'copyright'),
        'code': 'RGT',
    },
    'compliance': {
        'title': _('Compliance register'),
        'label': _('Compliance obligation'),
        'icon': 'check-shield',
        'categories': ('compliance',),
        'code': 'CMP',
    },
}
REGISTER_STAGES = (
    Status.ACCEPTED,
    Status.ASSIGNED,
    Status.REVIEW,
    Status.APPROVED,
    Status.COMPLETED,
    Status.CLOSED,
)


def module_for_category(category):
    return next((key for key, module in MODULES.items() if category in module['categories']), None)


def module_for(case):
    return module_for_category(case.matter.category_id)


def native_departments(policy):
    qs = Department.objects.filter(
        is_active=True,
        directorate__is_active=True,
        is_demo=policy.user.is_demo,
        directorate__is_demo=policy.user.is_demo,
    )
    return qs if head(policy) else qs.filter(pk__in=policy.departments('matter.department'))


def responsible_people(case):
    return User.objects.filter(
        department_id=case.matter.department_id,
        is_active=True,
        kind=User.Kind.EMPLOYEE,
        is_demo=case.matter.is_demo,
    ).order_by('first_name', 'last_name', 'username')


def may_supervise(policy, case):
    return supervises(policy, case) and (case.internal or policy.user.pk != case.submitted_by_id)


def may_operate(policy, case, assignment=None):
    assignment = assignment or current_assignment(case)
    return case.matter.status in REGISTER_STAGES and (
        may_supervise(policy, case)
        or (assignee(policy, assignment) and policy.user.pk != case.submitted_by_id)
    )


def strategy(policy, case):
    return head(policy) or assignee(policy, current_assignment(case))
