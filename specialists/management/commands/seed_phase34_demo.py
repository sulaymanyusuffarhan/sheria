"""Idempotent fictional specialist cases, using the same transactions as the UI."""

from datetime import datetime, time, timedelta
from uuid import NAMESPACE_URL, uuid5

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from casework import services as workflow
from casework.management.commands.seed_phase2_demo import _pdf, _session
from casework.models import Case, Document
from casework.storage import cipher
from identity.security import demo_available
from library import services as repository
from matters.models import Category, Classification
from specialists import services as specialist

REPLAY_REASON = 'Fictional exercise only. No court, regulator or institutional authority is asserted.'


def _data(case, values):
    case.refresh_from_db()
    return {'revision': case.revision, 'reason': REPLAY_REASON, **values}


class Command(BaseCommand):
    help = 'Seed fictional litigation, opinion, rights, compliance and versioned evidence. Requires seed_demo.'

    @transaction.atomic
    def handle(self, *args, **options):
        if not demo_available():
            raise CommandError('Refusing specialist seed outside an isolated development demo database.')
        try:
            cipher()
        except ValidationError as exc:
            raise CommandError('Set a separate demo LSMS_DOCUMENT_KEY.') from exc
        staff, senior, officer, head = [
            _session(role) for role in ('staff', 'senior', 'officer', 'head')
        ]
        today = timezone.localdate()
        due = today + timedelta(days=12)

        def open_case(key, module, category, title, *, public=False):
            token = uuid5(NAMESPACE_URL, f'https://demo.example.invalid/lsms/phase-34/{key}')
            if existing := Case.objects.filter(submission_key=token, matter__is_demo=True).first():
                return existing, False
            record = {
                'submission_key': token,
                'title': title,
                'category': Category.objects.get(pk=category),
                'summary': f'{title} is a fictional training example, not an actual TBC record.',
                'required_on': due,
                'priority': 'normal',
            }
            if public:
                case = workflow.submit(
                    staff,
                    record
                    | {
                        'office': 'Programme development',
                        'contact': 'staff@demo.example.invalid',
                        'declaration': True,
                    },
                )
                advance(case, senior, 'triage')
                advance(case, senior, 'accept', verified=True)
            else:
                case = specialist.open_internal(
                    senior,
                    module,
                    record
                    | {'department': staff.user.department, 'classification': Classification.INTERNAL},
                )
            advance(case, senior, 'assign', officer=officer.user, due_on=due, priority='normal')
            advance(case, officer, 'begin')
            return case, True

        def advance(case, actor, command, **values):
            return workflow.run(actor, case.pk, command, _data(case, values))

        def record(case, module, command, actor=officer, **values):
            return specialist.run(actor, case.pk, module, command, _data(case, values))

        def doc(case, title, category, filename, *, sealed=False):
            upload = _pdf(title, ['Fictional original for a controlled workflow exercise.'], filename)
            return repository.create(
                officer,
                case.pk,
                _data(
                    case,
                    {
                        'title': title,
                        'category': category,
                        'tags': 'training, demonstration',
                        'sealed': sealed,
                        'retain_until': today + timedelta(days=365),
                        'document': upload,
                        'provenance': 'Fictional original. Never use in a legal proceeding.',
                    },
                ),
            )

        docket, created = open_case(
            'docket', 'litigation', 'litigation', 'Studio transmission dispute (demo)'
        )
        if created:
            record(
                docket,
                'litigation',
                'register',
                court='Illustrative Civil Registry',
                external_number='EXERCISE/CIV/026',
                filed_on=today,
                case_type='Fictional civil claim',
                advocate='Illustrative counsel',
            )
            record(
                docket, 'litigation', 'lit_party', name='Fictional Media Cooperative', role='claimant'
            )
            record(
                docket, 'litigation', 'lit_party', name='Fictional Broadcast Office', role='respondent'
            )
            first = record(
                docket,
                'litigation',
                'lit_hearing',
                starts_at=timezone.make_aware(datetime.combine(today + timedelta(days=2), time(10, 15))),
                room='Training Court A',
                purpose='Original directions hearing',
            )
            record(
                docket,
                'litigation',
                'lit_reschedule',
                hearing=first,
                starts_at=timezone.make_aware(datetime.combine(today + timedelta(days=4), time(9, 30))),
                room='Training Court B',
                purpose='Directions hearing',
                reason='Fictional registry adjournment',
            )
            filing = doc(docket, 'Directions notice (demo)', 'court', 'directions-notice-demo.pdf')
            record(
                docket,
                'litigation',
                'lit_progress',
                kind='order',
                occurred_on=today,
                note='Recorded an illustrative directions order.',
                evidence=filing.versions.get().document,
            )
            record(
                docket,
                'litigation',
                'lit_progress',
                kind='strategy',
                occurred_on=today,
                note='Fictional privileged strategy marker for access verification.',
            )
            record(
                docket,
                'litigation',
                'lit_cost',
                occurred_on=today,
                description='Fictional registry fee',
                amount='35000.00',
                currency='TZS',
            )
            doc(
                docket,
                'Sealed witness notes (demo)',
                'court',
                'sealed-witness-notes-demo.pdf',
                sealed=True,
            )

        rights, created = open_case('rights', 'rights', 'broadcasting', 'Weekend music package (demo)')
        if created:
            record(
                rights,
                'rights',
                'register',
                description='Fictional programme acquisition, including a limited broadcast window and an illustrative renewal.',
            )
            proof = (
                doc(rights, 'Music licence provenance (demo)', 'rights', 'music-licence-demo.pdf')
                .versions.get()
                .document
            )
            terms = {
                'kind': 'music',
                'asset': 'Harbour Session (fictional)',
                'licensor': 'Fictional Harbour Studio',
                'platform': 'FM only',
                'territory': 'Tanzania',
                'terms': 'One live broadcast and one repeat in the stated term. No OTT or archive reuse.',
                'exclusive': False,
                'starts_on': today - timedelta(days=30),
                'ends_on': today + timedelta(days=5),
                'evidence': proof,
                'provenance_verified': True,
            }
            previous = record(rights, 'rights', 'rights_licence', **terms)
            record(
                rights,
                'rights',
                'rights_renew',
                licence=previous,
                **(
                    terms
                    | {'starts_on': today + timedelta(days=6), 'ends_on': today + timedelta(days=55)}
                ),
            )
            record(
                rights,
                'rights',
                'rights_incident',
                licence=previous,
                occurred_on=today,
                allegation='Illustrative request to replay outside the approved platform.',
                immediate_action='Preserve the training log; refer to the linked fictional dispute.',
                referred_matter=docket,
            )

        cmp, created = open_case(
            'compliance', 'compliance', 'compliance', 'Programme archive controls (demo)'
        )
        if created:
            record(
                cmp,
                'compliance',
                'register',
                requirement='For this exercise, reconcile sample archive access entries each review period.',
                control_area='Archive custody exercise',
                responsible=staff.user,
                due_on=today + timedelta(days=7),
            )
            version = record(
                cmp,
                'compliance',
                'cmp_source',
                title='Fictional archive training policy',
                citation='DEMO-ONLY / ARCHIVE-01',
                jurisdiction='Fictional control exercise',
                url='https://example.invalid/archive-training-policy',
                effective_on=today,
            )
            record(
                cmp,
                'compliance',
                'cmp_verify',
                senior,
                source_id=version.pk,
                verified=True,
                reason='Verified this is synthetic exercise input, not a source of law.',
            )
            record(
                cmp,
                'compliance',
                'cmp_assess',
                source_id=version.pk,
                result='gap',
                findings='The fictional reconciliation lacks one sample entry.',
                next_review=today + timedelta(days=18),
                due_on=today + timedelta(days=4),
                plan='Reconcile the missing training entry and attach a copy.',
                responsible=staff.user,
            )
            log = doc(
                cmp, 'Archive reconciliation (demo)', 'regulatory', 'archive-reconciliation-demo.pdf'
            )
            action = cmp.obligation.assessments.first().corrective_action
            record(
                cmp,
                'compliance',
                'cmp_remediate',
                action=action,
                summary='Fictional reconciliation ready for independent verification.',
                evidence=log.versions.get().document,
            )

        opinion, created = open_case(
            'opinion', 'opinions', 'opinion', 'Programme quotation opinion (demo)', public=True
        )
        if created:
            record(opinion, 'opinions', 'register')
            advance(
                opinion,
                officer,
                'upload',
                kind=Document.Kind.INTERNAL,
                document=_pdf(
                    'Fictional quotation notes',
                    ['Training case, not verified legal authority.'],
                    'quotation-notes-demo.pdf',
                ),
            )
            version = record(
                opinion,
                'opinions',
                'opinion_version',
                question='May the fictional programme reuse a training quotation?',
                facts='The sample scene uses an invented quotation under a fictional agreement.',
                authorities='Demonstration notes only; no citation to real law.',
                analysis='For this exercise, inspect the recorded scope and ask for additional authority if it is incomplete.',
                conclusion='Keep the sample within the training exercise. This is not actual legal advice.',
                evidence=opinion.documents.first(),
            )
            record(
                opinion,
                'opinions',
                'opinion_senior',
                senior,
                version_id=version.pk,
                verified=True,
                senior_outcome='endorse',
            )
            advance(
                opinion,
                officer,
                'submit_review',
                recommendation='approve',
                response='Your fictional exercise opinion is ready.',
                share_opinion=True,
                confirmed=True,
            )
            review = opinion.reviews.first()
            pin = {'review_id': review.pk, 'package_digest': review.digest}
            advance(
                opinion,
                head,
                'decide',
                **pin,
                outcome='clear',
                gates_verified=True,
                no_conflict=True,
                demo_confirmation=True,
            )
            advance(opinion, head, 'issue', **pin, confirmed=True)

        self.stdout.write(self.style.SUCCESS('Synthetic specialist walkthroughs are ready.'))
        for case in (docket, rights, cmp, opinion):
            self.stdout.write(f'{case.matter.reference}: {case.matter.title}')
