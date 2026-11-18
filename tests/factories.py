"""Small, synthetic fixtures for isolated test databases. Revisions: Git."""

import uuid

from django.test import Client
from django.utils import timezone

from identity.models import Department, Directorate, Role, RoleCode, RoleGrant, User


def division(code='TEST', *, is_demo=False):
    directorate = Directorate.objects.create(
        code=f'{code}-DIR', name=f'{code} directorate', name_sw=f'Kurugenzi {code}', is_demo=is_demo
    )
    return Department.objects.create(
        code=code,
        name=f'{code} department',
        name_sw=f'Idara {code}',
        directorate=directorate,
        is_demo=is_demo,
    )


def grant(account, code, department=None, **kwargs):
    return RoleGrant.objects.create(
        user=account, role=Role.objects.get(pk=code), department=department, **kwargs
    )


def employee(username, department=None, *, roles=(RoleCode.STAFF,), password=None, is_demo=False):
    account = User.objects.create_user(
        username=username,
        department=department,
        password=password,
        is_demo=is_demo,
        first_name=username.title(),
        last_name='Test',
        kind=User.Kind.TECHNICAL if RoleCode.ICT in roles else User.Kind.EMPLOYEE,
    )
    for code in roles:
        grant(
            account,
            code,
            department if code in (RoleCode.HOD, RoleCode.SENIOR, RoleCode.RECORDS) else None,
        )
    return account


def authenticate_as(user, *, client=None, verified_mfa=True):
    """Isolate authorisation cases from credential verification, which has separate tests."""
    if client is None:
        client = Client()
    client.force_login(user)
    session = client.session
    now = int(timezone.now().timestamp())
    session.update(
        dict(
            signed_in_at=now,
            last_activity_at=now,
            auth_channel='local',
            verified_mfa=verified_mfa,
            audit_session=str(uuid.uuid4()),
        )
    )
    session.save()
    return client
