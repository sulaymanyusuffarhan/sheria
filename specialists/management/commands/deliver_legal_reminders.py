"""Run one internal deadline-reminder pass; no external delivery or legal action."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from identity.security import demo_available
from specialists.reminders import send_due


class Command(BaseCommand):
    help = 'Deliver scoped in-app legal deadline reminders (idempotent, no external channels).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--demo', action='store_true', help='Only deliver for the synthetic demo, when enabled.'
        )

    def handle(self, *args, **options):
        demo = options['demo']
        if demo and not demo_available() or settings.DEMO_MODE and not demo:
            raise CommandError('Use --demo only with the enabled synthetic workspace.')
        sent = send_due(demo=demo)
        self.stdout.write(f'Delivered {sent} legal deadline reminder(s).')
