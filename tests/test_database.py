"""Migration compatibility and database transaction checks. Revisions: Git."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from identity.models import AccessRequest, AuditLog, RoleCode, RoleGrant, User
from identity.policy import Policy
from identity.services import apply_access
from matters.models import LegalRequest, StaffStatus

from .factories import division, employee


class DatabaseIntegrityTests(TransactionTestCase):
    serialized_rollback = True

    def test_upgrade_only_backfills_from_already_published_updates(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        earlier = [('matters', '0002_categories')]
        try:
            executor.migrate(earlier)
            apps = executor.loader.project_state(earlier).apps
            directory = apps.get_model('identity', 'Directorate').objects.create(
                code='LEG-DIR', name='Synthetic legacy directorate'
            )
            unit = apps.get_model('identity', 'Department').objects.create(
                code='LEG', name='Synthetic legacy unit', directorate=directory
            )
            user = apps.get_model('identity', 'User').objects.create(
                username='legacy-user', department=unit
            )
            category = apps.get_model('matters', 'Category').objects.get(pk='contract')
            matter = apps.get_model('matters', 'LegalMatter').objects.create(
                department=unit, category=category, status='head_review', reference='TEST-UPGRADE'
            )
            request = apps.get_model('matters', 'LegalRequest').objects.create(
                matter=matter, requester=user
            )
            updates = apps.get_model('matters', 'RequestUpdate').objects
            updates.create(
                request=request, status='information', published_at=timezone.now() - timedelta(days=1)
            )
            updates.create(
                request=request, status='approved', published_at=timezone.now() + timedelta(days=1)
            )
        finally:
            MigrationExecutor(connection).migrate(latest)
        record = LegalRequest.objects.get(pk=matter.pk)
        self.assertEqual(record.public_status, StaffStatus.INFORMATION)
        self.assertEqual(record.matter.status, 'head_review')
        self.assertEqual(record.updates.latest('published_at').status, StaffStatus.PROCESSING)

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_access_activation_commits_only_once(self):
        unit = division('LOCK')
        ict = employee('ict', roles=(RoleCode.ICT,))
        head = employee('head', unit, roles=(RoleCode.HEAD,))
        target = employee('target', unit)
        access = AccessRequest.objects.create(
            target=target,
            target_epoch=target.auth_epoch,
            requested_by=ict,
            decided_by=head,
            decided_at=timezone.now(),
            role_id=RoleCode.OFFICER,
            status=AccessRequest.Status.APPROVED,
            reason='Synthetic concurrency test.',
        )
        barrier = Barrier(2, timeout=15)

        def activate():
            close_old_connections()
            try:
                request = RequestFactory().post('/apply/')
                SessionMiddleware(lambda _: None).process_request(request)
                request.user = User.objects.get(pk=ict.pk)
                request.policy = Policy(request.user)
                request.request_id = uuid.uuid4()
                record = AccessRequest.objects.get(pk=access.pk)
                barrier.wait()
                try:
                    apply_access(request, record)
                except ValidationError:
                    return 'stale'
                return 'applied'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: activate(), range(2)))
        self.assertCountEqual(results, ['applied', 'stale'])
        self.assertEqual(RoleGrant.objects.filter(user=target, role_id=RoleCode.OFFICER).count(), 1)
        self.assertEqual(
            AuditLog.objects.filter(action='access_applied', target=str(access.pk)).count(), 1
        )
        target.refresh_from_db()
        self.assertEqual(target.auth_epoch, 1)
