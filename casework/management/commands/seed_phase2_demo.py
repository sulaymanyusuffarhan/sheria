"""Seed optional, explicitly synthetic work at useful points in the Phase 2 journey. Revisions: Git."""

from datetime import timedelta
from io import BytesIO
from uuid import NAMESPACE_URL, uuid5

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import RequestFactory
from django.utils import timezone
from reportlab.pdfgen.canvas import Canvas

from casework import checklist, services, storage
from casework.models import Case, Document
from identity.models import User
from identity.policy import Policy
from identity.security import demo_available
from matters.models import Category


def _session(role):
    actor = User.objects.filter(username=f'demo-{role}', is_demo=True, is_active=True).first()
    if not actor or actor.has_usable_password():
        raise CommandError('Run seed_demo on an isolated development database first.')
    request = RequestFactory().post('/demo-fixtures/')
    request.user, request.policy = actor, Policy(actor)
    request.session = {'auth_channel': 'demo'}
    return request


def _pdf(title, details, filename):
    output = BytesIO()
    document = Canvas(output, pageCompression=1)
    document.setTitle(title)
    document.setFillColorRGB(0.08, 0.19, 0.34)
    document.setFont('Helvetica-Bold', 19)
    document.drawString(48, 780, title)
    document.setFont('Helvetica', 11)
    for y, line in enumerate(['TBC LSMS demonstration only - not a legal instrument', *details]):
        document.drawString(48, 750 - (18 * y), line)
    document.save()
    return storage.prepare_upload(
        SimpleUploadedFile(filename, output.getvalue(), content_type='application/pdf')
    )


class Command(BaseCommand):
    help = 'Seed a fictional intake, checklist in progress, Head queue item and issued response. Requires seed_demo.'

    @transaction.atomic
    def handle(self, *args, **options):
        if not demo_available():
            raise CommandError('Refusing workflow seed outside an isolated development demo database.')
        staff, senior, officer, head = [
            _session(role) for role in ('staff', 'senior', 'officer', 'head')
        ]
        try:
            storage.cipher()
        except ValidationError as exc:
            raise CommandError('Set a separate demo LSMS_DOCUMENT_KEY.') from exc
        due = timezone.localdate() + timedelta(days=12)
        definitions = (
            (
                'training',
                'Regional training partnership',
                'mou',
                'Dossier in progress',
                'Fictional Training Centre',
            ),
            (
                'archive',
                'Audio archive transcription pilot',
                'contract',
                'Awaiting a Head decision',
                'Fictional Archive Studio',
            ),
            ('clip', 'Archive excerpt for staff training', 'advice', 'Response issued', ''),
            (
                'equipment',
                'Studio equipment servicing agreement',
                'contract',
                'New intake',
                'Fictional Workshop Cooperative',
            ),
        )
        for key, title, category, stage, party in definitions:
            token = uuid5(NAMESPACE_URL, f'https://demo.example.invalid/lsms/phase-2/{key}')
            if Case.objects.filter(submitted_by=staff.user, submission_key=token).exists():
                continue
            data = {
                'submission_key': token,
                'category': Category.objects.get(pk=category),
                'title': title,
                'office': 'Programme development',
                'contact': 'staff@demo.example.invalid',
                'required_on': due,
                'summary': f'{title} is a fictional scenario for demonstrating the Legal Services workflow.',
                'priority': 'normal',
                'declaration': True,
            }
            if party:
                data |= {
                    'counterparty': party,
                    'contract_type': 'mou' if category == 'mou' else 'content',
                    'currency': 'TZS',
                    'amount': '3500000.00' if category == 'contract' else '0.00',
                    'duration': 'Six months',
                    'draft': _pdf(
                        title, ['Version: 1', 'Review draft. Not for signature.'], f'{key}-draft.pdf'
                    ),
                }
            case = services.submit(staff, data)

            def advance(actor, command, **values):
                case.refresh_from_db()
                return services.run(
                    actor,
                    case.pk,
                    command,
                    {
                        'revision': case.revision,
                        'reason': 'Synthetic demonstration step; no institutional legal authority is asserted.',
                        **values,
                    },
                )

            if key != 'equipment':
                advance(senior, 'triage')
                advance(senior, 'accept', verified=True)
                advance(senior, 'assign', officer=officer.user, due_on=due, priority='normal')
                advance(officer, 'begin')
                if party:
                    version = services.latest_version(case)
                    for section in checklist.REVIEW_SECTIONS[: 2 if key == 'training' else None]:
                        case.refresh_from_db()
                        answers = {
                            id_: {
                                'choice': 'yes',
                                'remarks': 'Verified against the fictional scenario.',
                                'evidence': [version.draft_id] if id_ == 'I-01' else [],
                            }
                            for id_, _ in section.items
                        }
                        answers['I-08'] = (
                            {
                                'choice': 'na',
                                'remarks': 'No prior correspondence applies to this exercise.',
                                'evidence': [],
                            }
                            if section.code == 'I'
                            else answers.get('I-08')
                        )
                        if section.code != 'I':
                            answers.pop('I-08')
                        services.save_checklist(
                            officer,
                            case.pk,
                            version.pk,
                            section.code,
                            {'revision': case.revision, 'answers': answers},
                        )
                    case.refresh_from_db()
                    services.save_checklist(
                        officer,
                        case.pk,
                        version.pk,
                        'XI',
                        {
                            'revision': case.revision,
                            'risk_class': 'medium',
                            'risks': [
                                {
                                    'category': 'Rights',
                                    'description': 'Illustrative reuse limit.',
                                    'likelihood': 'low',
                                    'impact': 'medium',
                                    'mitigation': 'Record the permitted uses.',
                                    'responsible': 'Programme development',
                                }
                            ],
                        },
                    )
                else:
                    advance(
                        officer,
                        'upload',
                        kind=Document.Kind.RESPONSE,
                        document=_pdf(
                            title,
                            ['Illustration of a separately issued staff deliverable.'],
                            'training-response.pdf',
                        ),
                    )
                if key != 'training':
                    advance(
                        officer,
                        'submit_review',
                        recommendation='approve',
                        confirmed=True,
                        release_draft=bool(party),
                        response='The fictional review is complete. Use the released document for this demonstration only.',
                        deliverables=case.documents.filter(kind=Document.Kind.RESPONSE),
                    )
                if key == 'clip':
                    package = case.reviews.get()
                    pin = {'review_id': package.pk, 'package_digest': package.digest}
                    advance(
                        head,
                        'decide',
                        **pin,
                        outcome='clear',
                        demo_confirmation=True,
                        no_conflict=True,
                        gates_verified=True,
                    )
                    advance(head, 'issue', **pin, confirmed=True)
            self.stdout.write(f'{case.matter.reference}: {title} — {stage}')
        self.stdout.write(self.style.SUCCESS('Phase 2 demonstration records are ready.'))
