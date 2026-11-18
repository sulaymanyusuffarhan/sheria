"""WSGI entry point. Introduced: 2026-11-18; revisions: Git."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lsms.settings')
application = get_wsgi_application()
