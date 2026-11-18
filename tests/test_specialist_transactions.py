"""Competing workers must not duplicate hearing replacements or deadline notices."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Barrier
from uuid import uuid4

from django.db import close_old_connections, connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from casework.models import Event
from casework.services import Conflict
from identity.models import RoleCode
from matters.models import Category, Classification, LegalAssignment
from specialists.models import DeadlineNotice, Hearing
from specialists.reminders import send_due
from specialists.services import open_internal, run

from .factories import division, employee
from .test_casework_transactions import request_for


class SpecialistTransactionTests(TransactionTestCase):
    serialized_rollback = True

    @skipUnlessDBFeature('has_select_for_update')
    def test_rescheduling_and_reminders_commit_once_with_competing_workers(self):
        department = division('SPX')
        senior = employee('senior', department, roles=(RoleCode.SENIOR,))
        head = employee('head', department, roles=(RoleCode.HEAD,))
        officer = employee('officer', department, roles=(RoleCode.OFFICER,))
        due = timezone.localdate() + timedelta(days=3)
        request = request_for(senior)
        case = open_internal(
            request,
            'litigation',
            {
                'submission_key': uuid4(),
                'title': 'Synthetic docket',
                'summary': 'Synthetic',
                'category': Category.objects.get(pk='litigation'),
                'department': department,
                'classification': Classification.INTERNAL,
                'priority': 'normal',
                'required_on': due,
            },
        )
        LegalAssignment.objects.create(matter_id=case.pk, officer=officer, due_on=due)
        run(
            request,
            case.pk,
            'litigation',
            'register',
            {
                'revision': case.revision,
                'court': 'Synthetic registry',
                'filed_on': None,
                'external_number': '',
                'case_type': 'Synthetic',
                'advocate': '',
            },
        )
        case.refresh_from_db()
        hearing = run(
            request,
            case.pk,
            'litigation',
            'lit_hearing',
            {
                'revision': case.revision,
                'starts_at': timezone.make_aware(datetime.combine(due, time(9))),
                'purpose': 'Synthetic hearing',
                'room': 'Training court',
            },
        )
        case.refresh_from_db()

        def race(items, fn):
            barrier = Barrier(2, timeout=15)

            def worker(argument):
                close_old_connections()
                try:
                    barrier.wait()
                    return fn(argument)
                finally:
                    connections.close_all()

            with ThreadPoolExecutor(max_workers=2) as pool:
                return list(pool.map(worker, items))

        def reschedule(actor):
            try:
                run(
                    request_for(actor),
                    case.pk,
                    'litigation',
                    'lit_reschedule',
                    {
                        'revision': case.revision,
                        'hearing': hearing,
                        'reason': 'Synthetic change',
                        'starts_at': hearing.starts_at + timedelta(days=1),
                        'purpose': 'New date',
                        'room': 'Training court',
                    },
                )
            except Conflict:
                return 'stale'
            return 'saved'

        self.assertCountEqual(race([senior, head], reschedule), ['saved', 'stale'])
        hearing.refresh_from_db()
        self.assertEqual(hearing.status, Hearing.Status.ADJOURNED)
        self.assertEqual(Hearing.objects.filter(replaces=hearing).count(), 1)
        self.assertEqual(Event.objects.filter(command='sp_lit_reschedule', case_id=case.pk).count(), 1)
        self.assertCountEqual(race([None, None], lambda _: send_due(demo=False)), [0, 1])
        self.assertEqual(DeadlineNotice.objects.get().milestone_id, hearing.replacement.milestone_id)
