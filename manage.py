"""Command entry point for LSMS. Introduced: 2026-11-18; revisions: Git."""

import os
import sys

if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lsms.settings')
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
