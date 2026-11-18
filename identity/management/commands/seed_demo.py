"""Synthetic sandbox fixture; never runs outside demo mode. Introduced: 2026-11-18; revisions: Git."""

from datetime import datetime, time, timedelta
from random import Random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from identity.models import (
    AccessRequest,
    AuditLog,
    Department,
    Directorate,
    Notification,
    Role,
    RoleCode,
    RoleGrant,
    User,
)
from identity.security import demo_available
from matters.models import (
    TERMINAL,
    Classification,
    LegalAssignment,
    LegalMatter,
    LegalRequest,
    MatterAccess,
    Milestone,
    RequestUpdate,
    StaffStatus,
    Status,
)

PEOPLE = {
    'head': ('Rehema', 'Mashauri', 'LEGAL'),
    'ict': ('Daudi', 'Lugendo', None),
    'staff': ('Neema', 'Mtemi', 'CONTENT'),
    'hod': ('Amina', 'Kasanga', 'CONTENT'),
    'officer': ('Samira', 'Mwinyi', 'LEGAL'),
    'senior': ('Baraka', 'Mrema', 'LEGAL'),
    'management': ('Elia', 'Mwanjali', 'CORPORATE'),
    'auditor': ('Irene', 'Mhando', 'FINANCE'),
    'records': ('Fatuma', 'Sanga', 'LEGAL'),
    'operations': ('Joel', 'Bakari', 'OPERATIONS'),
    'procurement': ('Upendo', 'Mollel', 'PROCUREMENT'),
    'programmes': ('Zawadi', 'Mshana', 'CONTENT'),
    'finance': ('Salim', 'Bwire', 'FINANCE'),
    'regional': ('Nuru', 'Mapunda', 'REGIONAL'),
}
TOPICS = {
    'contract': [
        'Studio equipment maintenance agreement',
        'Production services agreement',
        'Digital archive support contract',
        'Radio transmitter service agreement',
        'Outside broadcast vehicle lease',
        'Documentary commissioning agreement',
        'Studio facilities maintenance contract',
    ],
    'opinion': [
        'Advisory request on archive access',
        'Opinion on proposed community programming',
        'Review of a standard sponsorship clause',
    ],
    'litigation': [
        'Synthetic dispute file — equipment delivery',
        'Synthetic dispute file — production services',
        'Synthetic dispute file — premises access',
    ],
    'broadcasting': [
        'Regional carriage rights renewal',
        'Documentary repeat broadcast clearance',
        'Digital distribution rights review',
    ],
    'ip': ['Archive footage reuse permissions', 'Music library licensing review'],
    'compliance': [
        'Internal register evidence review',
        'Policy alignment request for regional services',
    ],
    'mou': ['Training partnership memorandum', 'Community media collaboration memorandum'],
    'labour': ['Template review for staff secondment'],
    'advice': ['Advisory request on standard publicity releases'],
}


STATUS_FIXTURES = {
    Status.SUBMITTED: StaffStatus.AWAITING,
    Status.PRELIMINARY: StaffStatus.AWAITING,
    Status.INFORMATION: StaffStatus.INFORMATION,
    Status.COMPLETED: StaffStatus.COMPLETED,
    Status.REJECTED: StaffStatus.REJECTED,
    Status.CLOSED: StaffStatus.CLOSED,
}


class Command(BaseCommand):
    help = 'Create fictional accounts and legal register fixtures in an isolated development demonstration database.'

    @transaction.atomic
    def handle(self, *args, **options):
        if not demo_available():
            raise CommandError(
                'Refusing demo seed outside an isolated LSMS_DEMO=1 development database.'
            )
        now, today, rng = timezone.now(), timezone.localdate(), Random(37)
        dirs, departments, people = {}, {}, {}
        for code, en, sw in (
            ('DLS', 'Legal Services', 'Huduma za Sheria'),
            ('BCAST', 'Broadcasting Services', 'Huduma za Utangazaji'),
            ('CSS', 'Corporate Services', 'Huduma za Shirika'),
            ('TECH', 'Technical Services', 'Huduma za Ufundi'),
        ):
            dirs[code], _ = Directorate.objects.get_or_create(
                code=f'D-{code}', defaults={'name': en, 'name_sw': sw, 'is_demo': True}
            )
        for code, parent, en, sw in (
            ('LEGAL', 'DLS', 'Legal Services', 'Huduma za Sheria'),
            ('CONTENT', 'BCAST', 'Programmes & Content', 'Vipindi na Maudhui'),
            ('REGIONAL', 'BCAST', 'Regional Broadcasting', 'Utangazaji wa Mikoa'),
            ('CORPORATE', 'CSS', 'Corporate Affairs', 'Masuala ya Shirika'),
            ('PROCUREMENT', 'CSS', 'Procurement', 'Ununuzi'),
            ('FINANCE', 'CSS', 'Finance & Administration', 'Fedha na Utawala'),
            ('OPERATIONS', 'TECH', 'Broadcast Operations', 'Uendeshaji wa Utangazaji'),
        ):
            departments[code], _ = Department.objects.get_or_create(
                code=f'D-{code}',
                defaults={'name': en, 'name_sw': sw, 'is_demo': True, 'directorate': dirs[parent]},
            )
        role_map = Role.objects.in_bulk()
        for code, (name, family, department) in PEOPLE.items():
            account, created = User.objects.get_or_create(
                username=f'demo-{code}',
                defaults={
                    'first_name': name,
                    'last_name': family,
                    'email': f'{code}@demo.example.invalid',
                    'kind': User.Kind.TECHNICAL if code == 'ict' else User.Kind.EMPLOYEE,
                    'department': departments.get(department),
                    'is_demo': True,
                },
            )
            if created:
                account.set_unusable_password()
                account.save(update_fields=['password'])
                if code != 'ict':
                    RoleGrant.objects.create(user=account, role=role_map[RoleCode.STAFF])
                if code in role_map and code != 'staff':
                    scope = departments['CONTENT'] if code in ('hod', 'senior', 'records') else None
                    RoleGrant.objects.create(user=account, role=role_map[code], department=scope)
            people[code] = account
        if not LegalMatter.objects.filter(is_demo=True).exists():
            month = today.replace(day=1)
            months = [month]
            for _ in range(5):
                month = (month - timedelta(days=1)).replace(day=1)
                months.insert(0, month)
            months_with_counts = zip(months, (9, 12, 16, 19, 26, 24))
            offices = [people['officer'], people['senior'], people['head']]
            sponsors = [
                people[code]
                for code in (
                    'staff',
                    'hod',
                    'operations',
                    'procurement',
                    'programmes',
                    'finance',
                    'regional',
                )
            ]
            keys = list(TOPICS)
            index = 0
            for month, count in months_with_counts:
                for n in range(count):
                    index += 1
                    category = rng.choices(keys, (32, 18, 15, 12, 5, 6, 5, 3, 4))[0]
                    status = rng.choices(
                        [
                            Status.SUBMITTED,
                            Status.PRELIMINARY,
                            Status.INFORMATION,
                            Status.ACCEPTED,
                            Status.ASSIGNED,
                            Status.REVIEW,
                            Status.HEAD_REVIEW,
                            Status.APPROVED,
                            Status.COMPLETED,
                            Status.REJECTED,
                            Status.CLOSED,
                        ],
                        [7, 5, 7, 8, 7, 26, 12, 4, 20, 4, 2],
                    )[0]
                    sponsor = rng.choices(sponsors, (17, 9, 16, 15, 15, 14, 14))[0]
                    if month == today.replace(day=1) and n < 6:
                        sponsor = people['staff']
                        category = ('contract', 'broadcasting', 'opinion', 'mou', 'ip', 'contract')[n]
                        status = (
                            Status.REVIEW,
                            Status.INFORMATION,
                            Status.COMPLETED,
                            Status.PRELIMINARY,
                            Status.HEAD_REVIEW,
                            Status.ASSIGNED,
                        )[n]
                    if index == 46:
                        status = Status.REVIEW
                    title = rng.choice(TOPICS[category])
                    opened_day = min(today, month + timedelta(days=rng.randrange(0, 26)))
                    opened = timezone.make_aware(datetime.combine(opened_day, time(9, n % 60)))
                    if opened > now:
                        opened = now - timedelta(minutes=n + 1)
                    classification = (
                        Classification.RESTRICTED
                        if index % 23 == 0
                        else Classification.CONFIDENTIAL
                        if index % 13 == 0
                        else Classification.INTERNAL
                    )
                    priority = rng.choice(['normal', 'normal', 'high', 'low', 'normal', 'urgent'])
                    item = LegalMatter.objects.create(
                        reference=f'TBC/DLS/{opened_day.year}/{index:04}',
                        title=title,
                        internal_summary='Synthetic internal context for verifying that staff projections do not expose private legal information.',
                        status=status,
                        category_id=category,
                        department=sponsor.department,
                        is_demo=True,
                        classification=classification,
                        opened_at=opened,
                        priority=priority,
                        risk=rng.choice(['low', 'medium', 'medium', 'high'])
                        if category == 'contract'
                        else '',
                    )
                    due = today + timedelta(days=rng.randint(-10, 23))
                    if index % 4 != 0 and status not in (
                        Status.SUBMITTED,
                        Status.PRELIMINARY,
                        Status.ACCEPTED,
                    ):
                        officer = rng.choice(offices)
                        LegalAssignment.objects.create(
                            matter=item,
                            officer=officer,
                            starts_at=opened,
                            due_on=due,
                            ends_at=now if status in TERMINAL and opened < now else None,
                        )
                        if classification == Classification.CONFIDENTIAL or index == 46:
                            for person in {people['head'], officer}:
                                MatterAccess.objects.create(
                                    matter=item,
                                    user=person,
                                    granted_by=people['head'],
                                    classification=classification,
                                    expires_at=now + timedelta(days=90),
                                )
                    public_status = STATUS_FIXTURES.get(status, StaffStatus.PROCESSING)
                    record = LegalRequest.objects.create(
                        matter=item,
                        requester=sponsor,
                        title=title,
                        submitted_at=opened,
                        public_status=public_status,
                        due_on=max(today + timedelta(days=1), due) if status not in TERMINAL else None,
                        summary='Synthetic request for development testing. No legal advice or actual case facts are recorded.',
                        department_visible=classification == Classification.INTERNAL and index % 3 != 0,
                    )
                    RequestUpdate.objects.create(
                        request=record,
                        status=StaffStatus.AWAITING,
                        published_at=opened,
                        message='The request was recorded in the demonstration register.',
                        message_sw='Ombi lilirekodiwa kwenye rejesta ya maonesho.',
                    )
                    if public_status != StaffStatus.AWAITING:
                        RequestUpdate.objects.create(
                            request=record,
                            status=public_status,
                            published_at=max(opened, now - timedelta(hours=3)),
                            message='A tracking status is available for this synthetic request.',
                            message_sw='Hali ya ufuatiliaji wa ombi hili la maonesho inapatikana.',
                        )
                    if (
                        category in ('litigation', 'contract')
                        and status not in TERMINAL
                        and (index % 5 == 0 or index % 7 == 0)
                    ):
                        Milestone.objects.create(
                            matter=item,
                            kind=Milestone.Kind.HEARING
                            if category == 'litigation'
                            else Milestone.Kind.EXPIRY,
                            date=today + timedelta(days=2 + index % 24),
                        )
        for user in people.values():
            if not user.notifications.exists():
                Notification.objects.create(
                    recipient=user,
                    title='Welcome to the Phase 1 demonstration',
                    title_sw='Karibu kwenye maonesho ya awamu ya kwanza',
                    body='This is a synthetic account. Explore the authorised workspace without using real institutional information.',
                    body_sw='Hii ni akaunti ya maonesho. Tumia mazingira yaliyoidhinishwa bila taarifa halisi za taasisi.',
                )
                Notification.objects.create(
                    recipient=user,
                    title='Your workspace is ready',
                    title_sw='Eneo lako la kazi liko tayari',
                    body='Use your profile to change the language or notification badge.',
                    body_sw='Tumia wasifu kubadili lugha au alama ya taarifa.',
                    created_at=now - timedelta(days=1),
                )
        if not AccessRequest.objects.filter(target__is_demo=True).exists():
            for name, status, role, dept in (
                ('programmes', 'pending', RoleCode.OFFICER, None),
                ('finance', 'approved', RoleCode.RECORDS, departments['FINANCE']),
            ):
                target = people[name]
                AccessRequest.objects.create(
                    target=target,
                    role=role_map[role],
                    department=dept,
                    target_epoch=target.auth_epoch,
                    requested_by=people['ict'],
                    reason='Synthetic access request for demonstrating separation of duties.',
                    status=status,
                    decided_by=people['head'] if status == 'approved' else None,
                    decided_at=now if status == 'approved' else None,
                    decision_reason='Synthetic training example.' if status == 'approved' else '',
                )
        if not AuditLog.objects.filter(is_demo=True).exists():
            AuditLog.objects.create(
                area=AuditLog.Area.ORGANISATION,
                is_demo=True,
                action='unit_created',
                actor=people['ict'],
                actor_role='ict',
                target=str(dirs['DLS'].pk),
            )
        self.stdout.write(
            self.style.SUCCESS(
                f'Demonstration ready: {LegalMatter.objects.filter(is_demo=True).count()} synthetic matters; role sign-in at /login/.'
            )
        )
