"""Reproduce synthetic QA restore/timing evidence on disposable Unix-socket PostgreSQL.

This is not a backup or load-testing tool for deployed instances. It takes no target,
uses no real data or credentials, and discards its clusters and dump on exit.
Introduced 2026-11-18; revisions: Git.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from time import perf_counter
from urllib.parse import parse_qs, urlparse

import pgserver  # Optional Linux x86-64 tool; install pgserver==0.1.4 separately.
import psycopg
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
BIN = Path(pgserver.__file__).resolve().parent / 'pginstall' / 'bin'
CLEAN_ENV = {key: val for key, val in os.environ.items() if not key.startswith(('LSMS_', 'PG'))}


def run(arguments, env, *, capture=False):
    return subprocess.run(
        arguments, cwd=ROOT, env=env, check=True, text=True, capture_output=capture
    ).stdout


def cluster(stack, path, name, settings):
    server = pgserver.get_server(path, cleanup_mode='delete')
    stack.callback(server.cleanup)
    uri = server.get_uri()
    host = parse_qs(urlparse(uri).query)['host'][0]
    if not Path(host).is_absolute() or not Path(host).is_dir():
        raise RuntimeError('The QA cluster must be on a local Unix socket.')
    with psycopg.connect(uri, autocommit=True) as db:
        db.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
        version, identity = db.execute(
            'SELECT version(), system_identifier FROM pg_control_system()'
        ).fetchone()
    env = settings | {'LSMS_DB_HOST': host, 'LSMS_DB_NAME': name}
    pg_env = CLEAN_ENV | {'PGHOST': host, 'PGUSER': 'postgres', 'PGDATABASE': name}
    return env, pg_env, version.split(' on ')[0], identity


def worker(mode, env):
    return json.loads(run([PYTHON, __file__, '--internal', mode], env, capture=True))


def main(mode):
    from secrets import token_urlsafe

    # Ignore libpq settings inherited by the parent as well as child commands.
    for key in tuple(os.environ):
        if key.startswith('PG'):
            del os.environ[key]

    with TemporaryDirectory(prefix='lsms-qa-') as tmp, ExitStack() as source:
        root = Path(tmp)
        (root / '.synthetic').touch()
        settings = CLEAN_ENV | {
            'PYTHONPATH': str(ROOT),
            'DJANGO_SETTINGS_MODULE': 'lsms.settings',
            'LSMS_QA_ROOT': tmp,
            'LSMS_ENV': 'development',
            'LSMS_DEMO': '1',
            'LSMS_DB_ENGINE': 'postgresql',
            'LSMS_DB_USER': 'postgres',
            'LSMS_DB_SSLMODE': 'disable',
            'LSMS_SECRET_KEY': token_urlsafe(64),
            'LSMS_DOCUMENT_KEY': Fernet.generate_key().decode(),
        }
        env, pg_env, version, source_id = cluster(source, root / 'source', 'qa_source', settings)
        for command in ('migrate', 'seed_demo', 'seed_phase2_demo', 'seed_phase34_demo'):
            run([PYTHON, 'manage.py', command], env, capture=True)
        run([PYTHON, 'manage.py', 'deliver_legal_reminders', '--demo'], env, capture=True)
        if mode == 'performance':
            return {'postgresql': version, **worker('performance', env)}

        before = worker('manifest', env)
        dump = root / 'synthetic.dump'
        start = perf_counter()
        run([BIN / 'pg_dump', '-Fc', '--no-owner', '--no-privileges', '-f', dump], pg_env)
        dump_seconds = perf_counter() - start
        source.close()

        with ExitStack() as target:
            env, pg_env, _, target_id = cluster(target, root / 'restore', 'qa_restore', settings)
            if source_id == target_id:
                raise AssertionError('Recovery must be to a different cluster.')
            start = perf_counter()
            run(
                [
                    BIN / 'pg_restore',
                    '--exit-on-error',
                    '--no-owner',
                    '--no-privileges',
                    '-d',
                    'qa_restore',
                    dump,
                ],
                pg_env,
            )
            restore_seconds = perf_counter() - start
            run([PYTHON, 'manage.py', 'migrate', '--check'], env, capture=True)
            after = worker('manifest', env)
            if before != after:
                changed = sorted(
                    name
                    for name in before['models'].keys() | after['models'].keys()
                    if before['models'].get(name) != after['models'].get(name)
                )
                source_constraints = set(map(tuple, before['constraints']))
                target_constraints = set(map(tuple, after['constraints']))
                raise AssertionError(
                    f'Restore mismatch: tables={changed}; '
                    f'source-only constraints={sorted(source_constraints - target_constraints)}; '
                    f'target-only constraints={sorted(target_constraints - source_constraints)}.'
                )
            verify = worker('restored', env)
        return {
            'postgresql': version,
            'dump_bytes': dump.stat().st_size,
            'dump_seconds': round(dump_seconds, 3),
            'restore_seconds': round(restore_seconds, 3),
            'model_tables_compared': len(before['models']),
            'model_rows_compared': sum(row[0] for row in before['models'].values()),
            'database_constraints_compared': len(before['constraints']),
            'matters': before['models']['matters.legalmatter'][0],
            'cases': before['models']['casework.case'][0],
            'audit_rows': before['models']['identity.auditlog'][0],
            **verify,
        }


def signed_in(role):
    from django.conf import settings
    from django.test import Client

    client = Client(HTTP_HOST='localhost', enforce_csrf_checks=True)
    assert client.get('/login/').status_code == 200
    token = client.cookies[settings.CSRF_COOKIE_NAME].value
    response = client.post('/login/demo/', {'role': role, 'csrfmiddlewaretoken': token})
    assert response.status_code == 302 and response['Location'] == '/'
    return client


def canonical_constraint(sql):
    # PostgreSQL 16 deparses some CHECKs before dump as one text[] cast and,
    # after reparsing the same dump, as element casts on varchar literals.
    # Canonicalise only this literal-list rewrite; compare other DDL verbatim.
    literal = r"'(?:[^']|'')*'::character varying"
    return re.sub(
        rf'\(ARRAY\[({literal}(?:, {literal})*)\]\)::text\[\]',
        lambda match: (
            'ARRAY[' + ', '.join(f'({value})::text' for value in re.findall(literal, match[1])) + ']'
        ),
        sql,
    )


def manifest():
    from django.apps import apps
    from django.core.serializers.json import DjangoJSONEncoder
    from django.db import connection

    class Encoder(DjangoJSONEncoder):
        def default(self, obj):
            if isinstance(obj, (bytes, memoryview)):
                return {'bytes_sha256': sha256(obj).hexdigest()}
            return super().default(obj)

    models = {}
    for model in apps.get_models():
        digest = sha256()
        count = 0
        for row in model._base_manager.order_by('pk').values().iterator():
            digest.update(json.dumps(row, cls=Encoder, sort_keys=True, separators=(',', ':')).encode())
            digest.update(b'\n')
            count += 1
        models[model._meta.label_lower] = [count, digest.hexdigest()]
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid), convalidated
              FROM pg_constraint WHERE connamespace = 'public'::regnamespace
             ORDER BY 1, 2
        """)
        constraints = [
            (table, name, canonical_constraint(sql), validated)
            for table, name, sql, validated in cursor.fetchall()
        ]
    return {'models': models, 'constraints': constraints}


def restored():
    from django.core.exceptions import ValidationError
    from django.test import override_settings
    from django.urls import reverse

    from casework.models import Document, ReviewRound
    from casework.services import digest
    from casework.storage import read_blob
    from library.models import Version

    docs = list(Document.objects.select_related('blob'))
    assert docs
    for doc in docs:
        assert sha256(read_blob(doc)).hexdigest() == doc.checksum
    frozen = list(ReviewRound.objects.all())
    assert frozen and all(digest(review.package) == review.digest for review in frozen)
    with override_settings(DOCUMENT_KEY=Fernet.generate_key().decode()):
        try:
            read_blob(docs[0])
        except ValidationError:
            wrong_key_refused = True
        else:
            raise AssertionError('An unrelated key must not open evidence.')
    version = Version.objects.filter(item__sealed=True).latest('pk')
    client = signed_in('head')
    download = client.get(reverse('library_download', args=[version.item_id, version.pk]))
    assert download.status_code == 200
    assert sha256(download.content).hexdigest() == version.document.checksum
    frozen_review = next(review for review in frozen if review.version_id)
    pdf = client.get(reverse('checklist_export', args=[frozen_review.case_id, frozen_review.pk]))
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF-')
    from io import BytesIO

    from pypdf import PdfReader

    return {
        'documents_decrypted_and_checked': len(docs),
        'frozen_digests_recomputed': len(frozen),
        'wrong_key_refused': wrong_key_refused,
        'authorised_sealed_download_status': download.status_code,
        'frozen_pdf_export_status': pdf.status_code,
        'frozen_pdf_export_tag_tree_present': '/StructTreeRoot'
        in PdfReader(BytesIO(pdf.content)).root_object,
    }


def grow(size):
    from django.db import connection
    from django.utils import timezone

    from identity.models import Department, User
    from matters.models import Category, LegalMatter, LegalRequest, StaffStatus, Status

    departments = list(Department.objects.filter(is_demo=True).order_by('code'))
    staff = User.objects.get(username='demo-staff', is_demo=True)
    categories = list(Category.objects.order_by('code'))
    now = timezone.now()
    for start in range(0, size, 1000):
        batch = [
            LegalMatter(
                reference=f'DEMO/QA/{n:06d}',
                title=f'Synthetic QA {"Broadcast" if n % 10 == 0 else "Training"} {n}',
                department=staff.department if n % 4 == 0 else departments[n % len(departments)],
                category=categories[n % len(categories)],
                is_demo=True,
                status=Status.values[n % len(Status.values)],
                opened_at=now - timedelta(days=n % 300),
            )
            for n in range(start, min(start + 1000, size))
        ]
        LegalMatter.objects.bulk_create(batch)
        LegalRequest.objects.bulk_create(
            LegalRequest(
                matter=matter,
                requester=staff,
                title=matter.title,
                public_status=StaffStatus.PROCESSING,
                submitted_at=matter.opened_at,
            )
            for matter in batch
            if matter.department_id == staff.department_id and int(matter.reference[-6:]) % 4 == 0
        )
    with connection.cursor() as cursor:
        cursor.execute('ANALYZE')


def performance():
    from django.db import connection, connections
    from django.test import override_settings
    from django.test.utils import CaptureQueriesContext

    from matters.models import LegalMatter, LegalRequest

    endpoints = (
        ('legal_dashboard', 'head', '/admin/'),
        ('register_search', 'head', '/admin/matters/?q=Broadcast'),
        ('staff_requests', 'staff', '/portal/requests/'),
        ('court_calendar', 'head', '/admin/calendar/'),
        ('management_dashboard', 'management', '/admin/management/'),
        ('management_xlsx', 'management', '/admin/management/export/xlsx/'),
    )

    def series(pool, clients, path):
        barrier = Barrier(len(clients), timeout=30)

        def collect(client):
            try:
                barrier.wait()
                rows = []
                for _ in range(40 // len(clients)):
                    start = perf_counter()
                    response = client.get(path)
                    rows.append(
                        ((perf_counter() - start) * 1000, response.status_code, len(response.content))
                    )
                return rows
            finally:
                connections.close_all()

        rows = sorted(row for chunk in pool.map(collect, clients) for row in chunk)

        def rank(q):
            return round(rows[math.ceil(q * len(rows)) - 1][0], 1)

        return {
            'n': len(rows),
            'p50_ms': rank(0.5),
            'p95_ms': rank(0.95),
            'max_ms': rank(1),
            'non_200': sum(row[1] != 200 for row in rows),
            'response_bytes': rows[0][2],
        }

    def profile():
        output = {}
        for name, role, path in endpoints:
            clients = [signed_in(role) for _ in range(4)]
            client = clients[0]
            with CaptureQueriesContext(connection) as queries:
                assert client.get(path).status_code == 200
            with ThreadPoolExecutor(max_workers=4) as pool:
                output[name] = {
                    'sql_queries': len(queries),
                    'serial': series(pool, clients[:1], path),
                    'four_sessions': series(pool, clients, path),
                }
        return {
            'matters': LegalMatter.objects.count(),
            'staff_projections': LegalRequest.objects.count(),
            'results': output,
        }

    with override_settings(DEBUG=False):
        baseline = profile()
        grow(25_000)
        scaled = profile()
    return {'baseline': baseline, 'scaled': scaled}


def internal(mode):
    import django

    qa_root = Path(os.environ['LSMS_QA_ROOT'])
    if not qa_root.name.startswith('lsms-qa-') or not (qa_root / '.synthetic').is_file():
        raise RuntimeError('The replay only accepts a generated synthetic fixture.')
    django.setup()
    from django.conf import settings

    from identity.security import demo_available

    db = settings.DATABASES['default']
    if db['NAME'] not in ('qa_source', 'qa_restore') or not Path(db['HOST']).is_absolute():
        raise RuntimeError('The replay only uses a local QA PostgreSQL database.')
    if not demo_available():
        raise RuntimeError('Refusing to run on a non-demo database.')
    return {'manifest': manifest, 'restored': restored, 'performance': performance}[mode]()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('restore', 'performance'))
    parser.add_argument(
        '--internal', choices=('manifest', 'restored', 'performance'), help=argparse.SUPPRESS
    )
    if sys.argv[1:2] == ['--internal'] and len(sys.argv) == 3:
        data = internal(sys.argv[2])
    else:
        data = main(parser.parse_args().mode)
    print(json.dumps(data, sort_keys=True, indent=2))
