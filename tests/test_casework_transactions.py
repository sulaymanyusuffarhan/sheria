"""Race and atomic rollback tests for Phase 2. Revisions: Git."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.sessions.middleware import SessionMiddleware
from django.db import close_old_connections, connections
from django.test import RequestFactory, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from casework.models import Case, Event, EventNotice, ReferenceCounter
from casework.services import Conflict, run, submit
from identity.models import AuditLog, Notification, RoleCode, User
from identity.policy import Policy
from matters.models import Category, LegalMatter, LegalRequest, RequestUpdate, Status

from .factories import division, employee


def request_for(actor):
    request = RequestFactory().post('/synthetic/')
    SessionMiddleware(lambda _: None).process_request(request)
    request.user = User.objects.get(pk=actor.pk)
    request.policy = Policy(request.user)
    return request


def intake_data():
    return {
        'submission_key': uuid4(),
        'category': Category.objects.get(pk='advice'),
        'title': 'Concurrent synthetic submission',
        'office': 'Programme office',
        'contact': 'test@example.invalid',
        'summary': 'Synthetic background',
        'priority': 'normal',
        'declaration': True,
        'required_on': timezone.localdate() + timedelta(days=5),
    }


class WorkflowTransactionTests(TransactionTestCase):
    serialized_rollback = True

    def test_notification_failure_rolls_back_entire_submission(self):
        requester = employee('requester', division('TX'))
        with patch('casework.services.notices.notify', side_effect=RuntimeError('synthetic rollback')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic rollback'):
                submit(request_for(requester), intake_data())
        for model in (
            LegalMatter,
            LegalRequest,
            Case,
            RequestUpdate,
            Event,
            Notification,
            ReferenceCounter,
        ):
            self.assertFalse(model.objects.exists(), str(model))
        self.assertFalse(AuditLog.objects.filter(action='case_submit').exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_duplicate_post_and_competing_triage_commit_once(self):
        department = division('RACE')
        first = employee('requester', department)
        second = employee('other-requester', department)
        head = employee('head', department, roles=(RoleCode.HEAD,))
        senior = employee('senior', department, roles=(RoleCode.SENIOR,))
        data = intake_data()

        def race(items, fn):
            barrier = Barrier(2, timeout=15)

            def worker(actor):
                close_old_connections()
                try:
                    request = request_for(actor)
                    barrier.wait()
                    return fn(request)
                finally:
                    connections.close_all()

            with ThreadPoolExecutor(max_workers=2) as pool:
                return list(pool.map(worker, items))

        ids = race([first, first], lambda request: submit(request, data).pk)
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(Case.objects.count(), 1)
        self.assertEqual(Event.objects.filter(command='submit').count(), 1)
        self.assertEqual(EventNotice.objects.filter(event__command='submit').count(), 3)
        distinct_posts = race(
            [first, second], lambda request: submit(request, data | {'submission_key': uuid4()}).pk
        )
        self.assertEqual(len(set(distinct_posts)), 2)
        self.assertEqual(LegalMatter.objects.values('reference').distinct().count(), 3)

        def triage(request):
            try:
                run(request, ids[0], 'triage', {'revision': 1, 'reason': 'Synthetic competing triage.'})
            except Conflict:
                return 'stale'
            return 'saved'

        self.assertCountEqual(race([head, senior], triage), ['saved', 'stale'])
        self.assertEqual(Event.objects.filter(case_id=ids[0], command='triage').count(), 1)
        self.assertEqual(LegalMatter.objects.get(pk=ids[0]).status, Status.PRELIMINARY)
