"""Bounded, explicit Phase 2 command payloads. Revisions: Git."""

from datetime import date
from uuid import uuid4

from django import forms
from django.core.validators import RegexValidator
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from matters.models import Category, Classification, LegalMatter

from . import checklist
from .models import ContractType, Decision, Document, Recommendation
from .policy import CONTRACT_CATEGORIES, clean_documents, matter_documents, officer_choices
from .storage import prepare_upload


def text(label, maximum=160, *, rows=None, required=True, help_text=''):
    return forms.CharField(
        label=gettext_lazy(label),
        max_length=maximum,
        required=required,
        widget=forms.Textarea(attrs={'rows': rows}) if rows else forms.TextInput(),
        help_text=help_text,
    )


def day(label, *, required=True):
    return forms.DateField(
        label=gettext_lazy(label),
        required=required,
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )


def attest(label, *, required=True):
    return forms.BooleanField(label=gettext_lazy(label), required=required)


class PreparedFileField(forms.FileField):
    def __init__(self, label, **kwargs):
        kwargs.setdefault('help_text', gettext_lazy('PDF, DOCX or XLSX; up to 5 MB each.'))
        super().__init__(
            label=gettext_lazy(label),
            max_length=120,
            widget=forms.ClearableFileInput(attrs={'accept': '.pdf,.docx,.xlsx'}),
            **kwargs,
        )

    def clean(self, data, initial=None):
        upload = super().clean(data, initial)
        return prepare_upload(upload) if upload else None


class Categories(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return (obj.name_sw or obj.name) if self.language == 'sw' else obj.name


class ContractFields(forms.Form):
    counterparty = text('Counterparty name')
    contract_type = forms.ChoiceField(
        label=gettext_lazy('Contract type'), choices=[('', '—'), *ContractType.choices]
    )
    amount = forms.DecimalField(
        label=gettext_lazy('Contract amount'), min_value=0, max_digits=16, decimal_places=2
    )
    currency = forms.CharField(
        label=gettext_lazy('Currency code'),
        min_length=3,
        max_length=3,
        validators=[
            RegexValidator(r'^[A-Za-z]{3}\Z', gettext_lazy('Enter a three-letter currency code.'))
        ],
    )
    duration = text('Contract duration', 100)
    draft = PreparedFileField('Contract draft')

    def clean_currency(self):
        return self.cleaned_data['currency'].upper()


class SubmitForm(ContractFields):
    base_order = ('title', 'category', 'office', 'contact', 'summary', 'required_on', 'priority')
    contract_order = ('counterparty', 'contract_type', 'amount', 'currency', 'duration', 'draft')
    finish_order = ('attachment', 'declaration')
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    title = text('Request title')
    category = Categories(queryset=Category.objects.all(), label=gettext_lazy('Category'))
    office = text('Office or unit', 100)
    contact = text('Work email or telephone', 120)
    summary = text('Description and background', 1600, rows=5)
    required_on = day('Required completion date')
    priority = forms.ChoiceField(
        label=gettext_lazy('Requested priority'), choices=LegalMatter.Priority.choices
    )
    attachment = PreparedFileField('Supporting attachment', required=False)
    declaration = attest(
        'I confirm that this request is accurate and that I am authorised to submit these documents.'
    )

    def __init__(self, *args, actor, language, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].language = language
        self.initial |= {
            'submission_key': uuid4(),
            'contact': actor.email,
            'priority': LegalMatter.Priority.NORMAL,
        }
        self.fields['required_on'].widget.attrs['min'] = timezone.localdate().isoformat()
        is_contract = self.is_bound and self.data.get('category') in CONTRACT_CATEGORIES
        for key in self.contract_order:
            self.fields[key].required = is_contract
            self.fields[key].widget.attrs['data-contract-input'] = ''
        self.contract_mode = is_contract

    @property
    def intake_fields(self):
        return [self[key] for key in self.base_order]

    @property
    def contract_fields(self):
        return [self[key] for key in self.contract_order]

    @property
    def finish_fields(self):
        return [self[key] for key in self.finish_order]

    def clean(self):
        data = super().clean()
        if data.get('required_on') and data['required_on'] < timezone.localdate():
            self.add_error('required_on', _('The required completion date cannot be in the past.'))
        if data.get('draft') and not self.contract_mode:
            self.add_error('draft', _('Use the contract draft field only for a contract or MoU.'))
        return data


class RevisionForm(forms.Form):
    revision = forms.IntegerField(min_value=1, max_value=2_147_483_647, widget=forms.HiddenInput)

    def __init__(self, *args, case, **kwargs):
        self.case = case
        super().__init__(*args, **kwargs)
        self.initial['revision'] = case.revision


class ReasonForm(RevisionForm):
    reason = text('Internal reason', 1600, rows=3)


class AcceptForm(ReasonForm):
    verified = attest('I verified the request details, authority and required evidence.')


class DispositionForm(ReasonForm):
    public_message = text(
        'Staff-facing message',
        240,
        rows=3,
        help_text=gettext_lazy('Published to the requester. Do not include privileged information.'),
    )


class AskForm(DispositionForm):
    due_on = day('Response due date')


class ReplyForm(RevisionForm):
    reply = text('Your response', 1600, rows=4)
    attachment = PreparedFileField('Supporting attachment', required=False)
    draft = PreparedFileField('Replacement contract draft', required=False)

    def __init__(self, *args, replace_draft, **kwargs):
        super().__init__(*args, **kwargs)
        if not replace_draft:
            self.fields.pop('draft')


class OfficerChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.display_name


class AssignForm(ReasonForm):
    officer = OfficerChoice(queryset=None, label=gettext_lazy('Legal officer'))
    due_on = day('Review due date')
    priority = forms.ChoiceField(label=gettext_lazy('Priority'), choices=LegalMatter.Priority.choices)

    def __init__(self, *args, policy, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['officer'].queryset = officer_choices(self.case, policy)
        self.initial.update(
            priority=self.case.matter.priority,
            due_on=date.fromisoformat(self.case.submission['required_on'])
            if self.case.internal
            else self.case.matter.staff_request.due_on,
        )


class UploadForm(RevisionForm):
    kind = forms.ChoiceField(
        label=gettext_lazy('Document purpose'),
        choices=[
            (kind, Document.Kind(kind).label)
            for kind in (Document.Kind.INTERNAL, Document.Kind.RESPONSE)
        ],
    )
    document = PreparedFileField('Private document')


class RescanForm(RevisionForm):
    pending = forms.ModelChoiceField(queryset=None, label=gettext_lazy('Quarantined document'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pending'].queryset = matter_documents(self.case).filter(scan=Document.Scan.PENDING)


class VersionForm(RevisionForm, ContractFields):
    title = text('Contract title')
    reason = text('Reason for new version', 400, rows=3)

    def __init__(self, *args, version, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(['revision', 'title', *SubmitForm.contract_order, 'reason'])
        if version:
            for key in ('title', 'counterparty', 'contract_type', 'amount', 'currency', 'duration'):
                self.initial[key] = getattr(version, key)


class ReviewForm(RevisionForm):
    recommendation = forms.ChoiceField(
        label=gettext_lazy('Legal officer recommendation'), choices=[('', '—'), *Recommendation.choices]
    )
    reason = text('Internal rationale', 1600, rows=4)
    amendments = text('Required amendments or negotiation steps', 700, required=False, rows=3)
    condition_verification = text('Verification of earlier conditions', 1000, rows=3)
    response = text(
        'Proposed staff response',
        240,
        required=False,
        rows=3,
        help_text=gettext_lazy('Only the exact approved response will be published at issue.'),
    )
    deliverables = forms.ModelMultipleChoiceField(
        queryset=None,
        label=gettext_lazy('Proposed files for release'),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    share_opinion = attest('Propose issuing the full opinion to the named requester.', required=False)
    release_draft = attest(
        'Propose releasing the exact reviewed contract draft to the requester.', required=False
    )
    confirmed = attest(
        'I confirm this recommendation and its evidence are ready to freeze for Head review.'
    )

    def __init__(self, *args, version, prior_condition, **kwargs):
        super().__init__(*args, **kwargs)
        sensitive_opinion = (
            self.case.matter.category_id == 'opinion'
            and self.case.matter.classification != Classification.INTERNAL
        )
        if self.case.internal:
            self.fields.pop('response')
        if (
            self.case.internal
            or self.case.matter.category_id != 'opinion'
            or self.case.matter.classification != Classification.INTERNAL
        ):
            self.fields.pop('share_opinion')
        candidates = (
            Document.objects.none()
            if self.case.internal or sensitive_opinion
            else clean_documents(self.case).filter(kind=Document.Kind.RESPONSE)
        )
        if not candidates.exists():
            self.fields.pop('deliverables')
        else:
            self.fields['deliverables'].queryset = candidates
        if not version:
            self.fields.pop('release_draft')
        if not prior_condition:
            self.fields.pop('condition_verification')


class PackageForm(RevisionForm):
    review_id = forms.UUIDField(widget=forms.HiddenInput)
    package_digest = forms.CharField(min_length=64, max_length=64, widget=forms.HiddenInput)

    def __init__(self, *args, review, **kwargs):
        super().__init__(*args, **kwargs)
        self.review = review
        self.initial |= {'review_id': review.pk, 'package_digest': review.digest}


class ConfirmPackageForm(PackageForm):
    confirmed = attest('I confirm that only the reviewed response and selected versions will be issued.')


class StepUpForm(PackageForm):
    gates_verified = attest(
        'I verified all applicable institutional prerequisites and have recorded the basis.',
        required=False,
    )
    code = forms.CharField(
        label=gettext_lazy('Fresh one-time authenticator code'),
        min_length=6,
        max_length=6,
        validators=[RegexValidator(r'^[0-9]{6}\Z')],
        widget=forms.PasswordInput(
            attrs={'autocomplete': 'one-time-code', 'inputmode': 'numeric', 'pattern': '[0-9]{6}'}
        ),
    )
    demo_confirmation = attest(
        'I confirm this is a simulated decision on fictional data, not MFA or a legal signature.'
    )

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('code' if actor.is_demo else 'demo_confirmation')


class DecideForm(StepUpForm):
    outcome = forms.ChoiceField(
        label=gettext_lazy('Head decision'), choices=[('', '—'), *Decision.Outcome.choices]
    )
    reason = text('Internal decision rationale', 1600, rows=3)
    conditions = text('Conditions and required verification', 700, required=False, rows=3)
    condition_due = day('Condition due date', required=False)
    public_message = text('Staff-facing disposition if rejected', 240, required=False, rows=3)
    gate_evidence = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label=gettext_lazy('Escalation evidence'),
        help_text=gettext_lazy(
            'Required to clear a Strategic/Board-Level risk. Use evidence from this frozen package.'
        ),
    )
    no_conflict = attest(
        'I am independent of the submission and recommendation and have no conflict of interest.'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.case.internal:
            self.fields.pop('public_message')
        ids = [entry['id'] for entry in self.review.package['evidence']]
        if self.review.version_id and ids:
            self.fields['gate_evidence'].queryset = clean_documents(self.case).filter(
                pk__in=ids, kind=Document.Kind.INTERNAL
            )
        else:
            self.fields.pop('gate_evidence')
        self.order_fields(
            [
                'review_id',
                'package_digest',
                'revision',
                'outcome',
                'reason',
                'no_conflict',
                'gates_verified',
                'gate_evidence',
                'conditions',
                'condition_due',
                'public_message',
                'code',
                'demo_confirmation',
            ]
        )


class ExecutionForm(StepUpForm):
    document = PreparedFileField('Signed counterpart')
    signatories = text('Verified external signatories', 350, rows=2)
    signed_on = day('Date signed')
    effective_on = day('Effective date')
    expires_on = day('Expiry date', required=False)
    prerequisites = text('Authority and prerequisite evidence references', 1200, rows=4)
    text_matches = attest(
        'I compared the counterpart with the cleared version and verified that substantive terms are unchanged.'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['gates_verified'].required = True
        self.order_fields(
            [
                'review_id',
                'package_digest',
                'revision',
                'document',
                'signatories',
                'signed_on',
                'effective_on',
                'expires_on',
                'prerequisites',
                'text_matches',
                'gates_verified',
                'code',
                'demo_confirmation',
            ]
        )


class CloseForm(ReasonForm):
    retention_confirmed = attest(
        'I confirm that closing this request does not authorise disposal or override a legal hold.'
    )


class SectionForm(RevisionForm):
    MAX_RISKS = 4
    POST_FIELDS = ('manager', 'monitoring', 'archive')

    def __init__(self, *args, section, sheet, **kwargs):
        super().__init__(*args, **kwargs)
        self.section = section
        self.items = []
        self.risk_rows = []
        if section.code == 'XI':
            self.fields['risk_class'] = forms.ChoiceField(
                label=gettext_lazy('Risk classification'), choices=[('', '—'), *checklist.RISK_LEVELS]
            )
            self.initial['risk_class'] = sheet.risk_class
            for i in range(self.MAX_RISKS):
                keys = []
                existing = sheet.risks[i] if i < len(sheet.risks) else {}
                for name, label in zip(checklist.RISK_FIELDS, checklist.RISK_LABELS, strict=True):
                    key = f'risk_{i}_{name}'
                    if name in ('likelihood', 'impact'):
                        self.fields[key] = forms.ChoiceField(
                            label=label, required=False, choices=[('', '—'), *checklist.LEVELS]
                        )
                    else:
                        self.fields[key] = forms.CharField(
                            label=label,
                            max_length=240 if name in ('description', 'mitigation') else 100,
                            required=False,
                            widget=forms.Textarea(attrs={'rows': 2})
                            if name in ('description', 'mitigation')
                            else forms.TextInput(),
                        )
                    self.initial[key] = existing.get(name, '')
                    keys.append(key)
                self.risk_rows.append([self[key] for key in keys])
        else:
            evidence = clean_documents(self.case)
            if section.code == 'XIII':
                evidence = matter_documents(self.case).filter(scan=Document.Scan.CLEAN)
            for key, prompt in section.items:
                choice, remarks, proof = f'{key}_choice', f'{key}_remarks', f'{key}_evidence'
                self.fields[choice] = forms.ChoiceField(
                    label=gettext_lazy('Answer'),
                    required=False,
                    choices=[('', gettext_lazy('Unanswered')), *checklist.CHOICES],
                )
                self.fields[remarks] = text('Remarks', 500, required=False, rows=2)
                self.fields[proof] = forms.ModelMultipleChoiceField(
                    label=gettext_lazy('Supporting evidence'),
                    queryset=evidence,
                    required=False,
                    widget=forms.SelectMultiple(attrs={'size': 3}),
                )
                saved = sheet.answers.get(key, {})
                self.initial |= {
                    choice: saved.get('choice', ''),
                    remarks: saved.get('remarks', ''),
                    proof: saved.get('evidence', []),
                }
                self.items.append(
                    {'key': key, 'label': prompt, 'fields': [self[choice], self[remarks], self[proof]]}
                )
            if section.code == 'XIII':
                for name, label in zip(
                    self.POST_FIELDS,
                    (
                        'Authorised contract manager',
                        'Monitoring and follow-up plan',
                        'Archive reference for signed original',
                    ),
                    strict=True,
                ):
                    self.fields[name] = text(label, 350, required=False)
                    self.initial[name] = sheet.post_details.get(name, '')
                self.fields['finalize'] = attest(
                    'Finalise and lock the post-execution review.', required=False
                )

    @property
    def post_fields(self):
        return (
            [self[name] for name in (*self.POST_FIELDS, 'finalize')]
            if self.section.code == 'XIII'
            else []
        )

    def clean(self):
        data = super().clean()
        if self.section.code == 'XI':
            risks = []
            for i in range(self.MAX_RISKS):
                row = {name: data.get(f'risk_{i}_{name}', '') for name in checklist.RISK_FIELDS}
                if any(row.values()):
                    if not all(row.values()):
                        self.add_error(None, _('Complete all fields in every risk row you use.'))
                    risks.append(row)
            data['risks'] = risks
        else:
            answers = {}
            for key, _prompt in self.section.items:
                choice = data.get(f'{key}_choice') or ''
                remarks = data.get(f'{key}_remarks') or ''
                docs = list(data.get(f'{key}_evidence') or ())
                if len(docs) > 3:
                    self.add_error(
                        f'{key}_evidence', _('Select at most three evidence files per check.')
                    )
                if not choice and (remarks or docs):
                    self.add_error(
                        f'{key}_choice', _('Select an answer before adding remarks or evidence.')
                    )
                answers[key] = {'choice': choice, 'remarks': remarks, 'evidence': docs}
            data['answers'] = answers
            if self.section.code == 'XIII':
                data['post_details'] = {key: data.get(key) or '' for key in self.POST_FIELDS}
        return data


COMMAND_FORMS = {
    'triage': ReasonForm,
    'accept': AcceptForm,
    'reject_intake': DispositionForm,
    'ask': AskForm,
    'verify': ReasonForm,
    'assign': AssignForm,
    'begin': ReasonForm,
    'note': ReasonForm,
    'upload': UploadForm,
    'rescan': RescanForm,
    'version': VersionForm,
    'submit_review': ReviewForm,
    'decide': DecideForm,
    'issue': ConfirmPackageForm,
    'reopen': ReasonForm,
    'execute': ExecutionForm,
    'close': CloseForm,
}
