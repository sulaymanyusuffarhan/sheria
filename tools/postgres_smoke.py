"""Run LSMS tests against a temporary, local-only PostgreSQL cluster. Revisions: Git."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

import pgserver  # Optional Linux x86-64 smoke helper (pgserver==0.1.4).
import psycopg


def main():
    root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix='lsms-pg-') as temp:
        server = pgserver.get_server(Path(temp) / 'data', cleanup_mode='delete')
        try:
            uri = server.get_uri()
            host = parse_qs(urlparse(uri).query)['host'][0]
            with psycopg.connect(uri, autocommit=True) as connection:
                print('Temporary PostgreSQL:', connection.info.server_version, flush=True)
                connection.execute('CREATE DATABASE lsms')
            env = os.environ | {
                'LSMS_ENV': 'test',
                'LSMS_DEMO': '0',
                'LSMS_PREVIEW': '0',
                'LSMS_DB_ENGINE': 'postgresql',
                'LSMS_DB_HOST': host,
                'LSMS_DB_PORT': '',
                'LSMS_DB_NAME': 'lsms',
                'LSMS_DB_USER': 'postgres',
                'LSMS_DB_PASSWORD': '',
                'LSMS_DB_SSLMODE': 'disable',
            }
            subprocess.run(
                [sys.executable, 'manage.py', 'test', '-v', '2', *sys.argv[1:]],
                cwd=root,
                env=env,
                check=True,
            )
        finally:
            server.cleanup()


if __name__ == '__main__':
    main()
