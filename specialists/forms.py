"""Bounded forms; command fields are never generic model mutations."""

from uuid import uuid4

from django import forms
from django.core.validators import RegexValidator, URLValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from casework.forms import OfficerChoice, RevisionForm, attest, day, text
from casework.policy import clean_documents, head
from matters.models import Category, Classification, LegalMatter

from .models import Assessment, Hearing, Licence, LitigationOutcome, Party, Proceeding
from .policy import MODULES, native_departments, responsible_people, strategy
from .services import (
    corporate_contracts,
    latest_draft,
    latest_source,
    litigation_referrals,
    outstanding_actions,
)


class InternalForm(forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    title = text('Matter title')
    category = forms.ModelChoiceField(queryset=Category.objects.none(), label=_('Category'))
    department = forms.ModelChoiceField(queryset=None, label=_('Department'))
    classification = forms.ChoiceField(
        label=_('Classification'),
        choices=[('', '—'), *Classification.choices],
        help_text=_(
            'Protected matters require named clearance. Creating one does not grant you access.'
        ),
    )
    summary = text('Internal background', 1600, rows=4)
    required_on = day('Review due date')
    priority = forms.ChoiceField(label=_('Priority'), choices=LegalMatter.Priority.choices)

    def __init__(self, *args, module, policy, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = Category.objects.filter(pk__in=MODULES[module]['categories'])
        self.fields['department'].queryset = native_departments(policy)
        self.initial |= {
            'submission_key': uuid4(),
            'priority': LegalMatter.Priority.NORMAL,
            'department': policy.user.department_id,
            'category': MODULES[module]['categories'][0],
        }


class PickLabel(forms.ModelChoiceField):
    def __init__(self, *args, labeler, **kwargs):
        self.labeler = labeler
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        return self.labeler(obj)


def choice(label, choices, *, selected=False):
    return forms.ChoiceField(label=label, choices=[('', '—'), *choices] if not selected else choices)


def evidence(case, label=_('Supporting evidence'), *, required=False, repository=True):
    from library.services import specialist_files

    qs = specialist_files(case) if repository else clean_documents(case)
    return forms.ModelChoiceField(queryset=qs, label=label, required=required)


def datetime_input(label):
    return forms.DateTimeField(
        label=label,
        input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
    )


class CommandForm(RevisionForm):
    def __init__(self, *args, module, command, record, policy, **kwargs):
        super().__init__(*args, **kwargs)
        case = self.case
        form_fields = self.fields
        reason = text('Reason or disposition', 1200, rows=4)
        occurred = day('Event date')
        if command == 'register':
            if module == 'litigation':
                form_fields.update(
                    court=text('Court and registry', 140),
                    external_number=text('Court case number', 85, required=False),
                    case_type=text('Case type', 100),
                    filed_on=day('Filing date', required=False),
                    advocate=text('External advocate', required=False),
                )
            elif module == 'rights':
                form_fields.update(description=text('Portfolio description', 1000, rows=4))
            elif module == 'compliance':
                form_fields.update(
                    requirement=text('Obligation or control', 1700, rows=4),
                    control_area=text('Control area', 100),
                    responsible=OfficerChoice(
                        queryset=responsible_people(case), label=_('Responsible officer')
                    ),
                    due_on=day('Obligation deadline'),
                )
        elif command == 'lit_party':
            form_fields.update(name=text('Party name'), role=choice(_('Party role'), Party.Role.choices))
        elif command in ('lit_hearing', 'lit_reschedule', 'lit_hearing_result'):
            if command != 'lit_hearing':
                form_fields['hearing'] = PickLabel(
                    queryset=record.hearings.filter(status=Hearing.Status.SCHEDULED),
                    label=_('Scheduled hearing'),
                    labeler=lambda h: f'{timezone.localtime(h.starts_at):%d/%m/%Y %H:%M} · {h.purpose}',
                )
            if command != 'lit_hearing_result':
                form_fields.update(
                    starts_at=datetime_input(_('Hearing date and time (EAT)')),
                    room=text('Court room or location', 130),
                    purpose=text('Hearing purpose', 180),
                )
            else:
                form_fields['hearing_status'] = choice(
                    _('Disposition'),
                    [(h.value, h.label) for h in (Hearing.Status.HELD, Hearing.Status.CANCELLED)],
                )
            if command != 'lit_hearing':
                form_fields['reason'] = reason
        elif command == 'lit_progress':
            kinds = (
                Proceeding.Kind.choices
                if strategy(policy, case)
                else [pair for pair in Proceeding.Kind.choices if pair[0] != Proceeding.Kind.STRATEGY]
            )
            form_fields.update(
                kind=choice(_('Proceeding type'), kinds),
                occurred_on=occurred,
                note=text('Proceeding details', 2200, rows=6),
                evidence=evidence(case),
            )
        elif command == 'lit_cost':
            form_fields.update(
                occurred_on=occurred,
                description=text('Cost description', 180),
                amount=forms.DecimalField(
                    label=_('Amount'), max_digits=16, decimal_places=2, min_value=0
                ),
                currency=forms.CharField(
                    label=_('Currency code'),
                    min_length=3,
                    max_length=3,
                    validators=[RegexValidator(r'^[A-Za-z]{3}\Z')],
                ),
            )
            self.initial['currency'] = 'TZS'
        elif command == 'lit_outcome':
            kinds = (
                LitigationOutcome.Kind.choices
                if head(policy)
                else [
                    pair
                    for pair in LitigationOutcome.Kind.choices
                    if pair[0] != LitigationOutcome.Kind.SETTLEMENT
                ]
            )
            form_fields.update(
                kind=choice(_('Outcome type'), kinds),
                occurred_on=occurred,
                reason=reason,
                evidence=evidence(case, _('External disposition evidence'), required=True),
                authority=evidence(case, _('Separate settlement authority')),
                externally_verified=attest(
                    'I verified the external disposition. This form does not authorise a settlement.'
                ),
            )
            if head(policy):
                if policy.user.is_demo:
                    form_fields['demo_confirmation'] = attest(
                        'I confirm this is a simulated decision on fictional data, not MFA or a legal signature.',
                        required=False,
                    )
                else:
                    form_fields['code'] = forms.CharField(
                        label=_('Fresh one-time authenticator code'),
                        required=False,
                        max_length=6,
                        validators=[RegexValidator(r'^[0-9]{6}\Z')],
                        widget=forms.PasswordInput(
                            attrs={'inputmode': 'numeric', 'autocomplete': 'one-time-code'}
                        ),
                    )
        elif command == 'lit_close':
            form_fields.update(
                reason=reason,
                confirmed=attest(
                    'I verified appeals, outstanding obligations and custody. No records will be deleted.'
                ),
            )
        elif command == 'opinion_version':
            form_fields.update(
                question=text('Question presented', 300, rows=2),
                facts=text('Verified facts and scope', 2200, rows=4),
                authorities=text('Authorities and citations', 1800, rows=4),
                analysis=text('Opinion analysis', 3600, rows=7),
                conclusion=text('Conclusion', 1200, rows=4),
                evidence=evidence(case, repository=False),
            )
            if prior := latest_draft(case):
                self.initial.update(
                    {
                        key: getattr(prior, key)
                        for key in (
                            'question',
                            'facts',
                            'authorities',
                            'analysis',
                            'conclusion',
                            'evidence',
                        )
                    }
                )
        elif command == 'opinion_senior':
            form_fields.update(
                version_id=forms.UUIDField(widget=forms.HiddenInput),
                senior_outcome=choice(
                    _('Senior disposition'),
                    [('endorse', _('Endorse for Head review')), ('return', _('Return for revisions'))],
                ),
                reason=reason,
                verified=attest(
                    'I independently reviewed this version, its facts and applicable sources.'
                ),
            )
            self.initial['version_id'] = latest_draft(case).pk
        elif command in ('rights_licence', 'rights_renew'):
            if command == 'rights_renew':
                form_fields['licence'] = forms.ModelChoiceField(
                    queryset=record.licences.filter(renewal__isnull=True), label=_('Previous licence')
                )
            form_fields.update(
                kind=choice(_('Rights type'), Licence.Kind.choices),
                asset=text('Asset or programme', 180),
                licensor=text('Licensor or rights holder'),
                platform=text('Platform / medium', 140),
                territory=text('Territory', 140),
                terms=text('Permitted use, reuse and restrictions', 1700, rows=5),
                exclusive=attest('Exclusive rights', required=False),
                starts_on=day('Effective date'),
                ends_on=day('Expiry date'),
                evidence=evidence(case, _('Licence provenance'), required=True),
                provenance_verified=attest(
                    'I verified the source, applicable rights and recorded restrictions. This entry does not grant new rights.'
                ),
                execution=PickLabel(
                    queryset=corporate_contracts(policy, case),
                    label=_('Related executed contract version'),
                    required=False,
                    labeler=lambda row: f'{row.version.contract.reference} · v{row.version.number}',
                ),
            )
        elif command == 'rights_incident':
            form_fields.update(
                licence=forms.ModelChoiceField(
                    queryset=record.licences.all(), label=_('Affected licence'), required=False
                ),
                occurred_on=occurred,
                allegation=text('Reported infringement or rights concern', 1400, rows=4),
                immediate_action=text('Immediate response', 1000, rows=3),
                evidence=evidence(case),
                referred_matter=PickLabel(
                    queryset=litigation_referrals(policy, case),
                    label=_('Related litigation matter'),
                    required=False,
                    labeler=lambda row: f'{row.matter.reference} · {row.matter.title}',
                ),
            )
        elif command == 'rights_resolve':
            form_fields.update(
                incident=PickLabel(
                    queryset=record.incidents.filter(resolution__isnull=True),
                    label=_('Open incident'),
                    labeler=lambda row: f'{row.reported_on:%d/%m/%Y} · {row.allegation[:65]}',
                ),
                reason=reason,
            )
        elif command == 'cmp_source':
            form_fields.update(
                title=text('Source title', 180),
                citation=text('Official citation or reference', 180),
                jurisdiction=text('Jurisdiction', 100),
                url=forms.URLField(
                    label=_('Authoritative HTTPS source'),
                    max_length=240,
                    assume_scheme='https',
                    validators=[URLValidator(schemes=['https'])],
                ),
                effective_on=day('Effective date'),
            )
        elif command == 'cmp_verify':
            form_fields.update(
                source_id=forms.IntegerField(widget=forms.HiddenInput),
                reason=reason,
                verified=attest(
                    'I independently verified the source, jurisdiction, applicability and effective date.'
                ),
            )
            self.initial['source_id'] = latest_source(record).pk
        elif command == 'cmp_assess':
            form_fields.update(
                source_id=forms.IntegerField(widget=forms.HiddenInput),
                result=choice(_('Assessment result'), Assessment.Result.choices),
                findings=text('Assessment findings', 1700, rows=5),
                evidence=evidence(case),
                next_review=day('Next review date'),
                responsible=OfficerChoice(
                    queryset=responsible_people(case), label=_('Corrective action owner'), required=False
                ),
                due_on=day('Corrective action deadline', required=False),
                plan=text('Corrective action plan', 1100, rows=4, required=False),
            )
            self.initial['source_id'] = latest_source(record).pk
        elif command == 'cmp_remediate':
            form_fields.update(
                action=self._actions(record),
                summary=text('Corrective evidence summary', 1200, rows=4),
                evidence=evidence(case, required=True),
            )
        elif command == 'cmp_verify_action':
            # Select a pending evidence submission by its exact ID, not by a mutable latest pointer.
            from .models import Remediation

            qs = Remediation.objects.filter(
                action__in=outstanding_actions(record), decision__isnull=True
            ).exclude(submitted_by=policy.user)
            form_fields.update(
                remediation=PickLabel(
                    queryset=qs.select_related('submitted_by'),
                    label=_('Evidence submission to verify'),
                    labeler=lambda row: (
                        f'#{row.pk} · {row.submitted_by.display_name} · {row.summary[:55]}'
                    ),
                ),
                verdict=choice(
                    _('Verification decision'),
                    [
                        ('verify', _('Verify and resolve action')),
                        ('return', _('Request different evidence')),
                    ],
                ),
                reason=reason,
            )

    def _actions(self, record):
        return PickLabel(
            queryset=outstanding_actions(record).select_related('responsible'),
            label=_('Open corrective action'),
            labeler=lambda row: (
                f'{row.responsible.display_name} · {row.due_on:%d/%m/%Y} · {row.plan[:60]}'
            ),
        )

    def clean(self):
        data = super().clean()
        if data.get('currency'):
            data['currency'] = data['currency'].upper()
        if submitted := data.get('remediation'):
            data['action'], data['remediation_id'] = submitted.action, submitted.pk
        return data
