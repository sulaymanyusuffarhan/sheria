"""TBC/DLS/CRF/001/2026 · DLS · Internal Use Only.

Revision 1 transcribes the available checklist inventory (architecture/workflows §3).
Controlled master wording and translated form labels require DLS verification.
Published keys and prompts are immutable: introduce a new revision for edits.
"""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, override

FORM_REFERENCE = 'TBC/DLS/CRF/001/2026'
TEMPLATE_REVISION = 1
IDENTITY_FIELDS = (
    ('title', gettext_lazy('Contract title')),
    ('reference', gettext_lazy('Contract reference')),
    ('directorate', gettext_lazy('Requesting directorate')),
    ('department', gettext_lazy('Requesting department')),
    ('responsible_officer', gettext_lazy('Responsible officer')),
    ('counterparty', gettext_lazy('Counterparty name')),
    ('contract_type', gettext_lazy('Contract type')),
    ('value', gettext_lazy('Contract value and currency')),
    ('duration', gettext_lazy('Contract duration')),
    ('submitted_on', gettext_lazy('Date submitted')),
    ('required_on', gettext_lazy('Required completion date')),
    ('assigned_to', gettext_lazy('Assigned legal officer')),
)


@dataclass(frozen=True)
class Section:
    code: str
    title: str
    prompts: tuple[str, ...] = ()

    @property
    def items(self):
        return tuple((f'{self.code}-{i:02}', prompt) for i, prompt in enumerate(self.prompts, 1))


# Text is the recorded source inventory rendered as review prompts; not a statement of law.
SECTIONS = (
    Section('A', gettext_lazy('Contract Identification Details')),
    Section(
        'I',
        gettext_lazy('Preliminary Review'),
        (
            'Has the draft contract been submitted?',
            'Has the business case or justification been provided?',
            'Has the requesting department approved the submission?',
            'Has the relevant budget or funding evidence been provided?',
            'Are the applicable procurement documents attached?',
            'Are the applicable technical specifications or related documents attached?',
            'Are prior agreements and amendments attached where applicable?',
            'Has relevant correspondence been included?',
        ),
    ),
    Section(
        'II',
        gettext_lazy('Contracting Parties Review'),
        (
            'Are the parties identified by their correct legal names?',
            'Have registration and incorporation details been verified?',
            'Are addresses and contact details correct?',
            'Do the parties have legal capacity to enter the contract?',
            'Have the authorised signatories been identified and verified?',
            'Has any required power of attorney or other authority been verified?',
            'Have potential conflicts of interest been checked and addressed?',
        ),
    ),
    Section(
        'III',
        gettext_lazy('Legal and Regulatory Compliance Review'),
        (
            'Has the contract been reviewed against the applicable TBC Act and TBC mandate?',
            'Have the applicable public procurement requirements been reviewed?',
            'Have the applicable public finance requirements been reviewed?',
            'Have EPOCA and applicable broadcasting requirements been reviewed?',
            'Have copyright and intellectual property requirements been reviewed?',
            'Have applicable tax requirements been reviewed?',
            'Have applicable personal data protection requirements been reviewed?',
            'Has consistency with applicable TBC policies been reviewed?',
        ),
    ),
    Section(
        'IV',
        gettext_lazy('Contract Terms Review'),
        (
            'Are the purpose and scope clearly defined?',
            'Are the deliverables clearly specified?',
            'Are performance indicators and service standards measurable?',
            'Are the milestones and timeframes defined?',
            'Are the parties’ responsibilities and obligations allocated?',
            'Are reporting responsibilities and intervals set out?',
            'Are testing, acceptance and approval criteria defined?',
        ),
    ),
    Section(
        'V',
        gettext_lazy('Financial Review'),
        (
            'Is the total contract value stated?',
            'Are pricing and payment terms clear?',
            'Are payments linked to agreed performance or milestones where applicable?',
            'Has budget availability and alignment been confirmed?',
            'Are applicable taxes and deductions addressed?',
            'Is the currency and any exchange treatment stated?',
            'Are penalties and late payment terms clear?',
            'Have hidden, contingent and additional costs been checked?',
        ),
    ),
    Section(
        'VI',
        gettext_lazy('Intellectual Property and Content Review'),
        (
            'Is intellectual property ownership clearly allocated?',
            'Are copyright ownership and licensing terms clearly stated?',
            'Are broadcasting and retransmission rights defined?',
            'Are digital and OTT platform rights defined?',
            'Are archival, repeat-use and reuse rights defined?',
            'Are geographic territories and rights periods defined?',
            'Are exclusivity and related restrictions clearly stated?',
            'Have necessary third-party permissions and clearances been verified?',
        ),
    ),
    Section(
        'VII',
        gettext_lazy('Risk Allocation Review'),
        (
            'Is liability allocated clearly between the parties?',
            'Have financial and operational exposures been assessed?',
            'Are indemnities appropriate and clearly defined?',
            'Are limits and exclusions of liability reviewed?',
            'Have applicable insurance requirements and evidence been reviewed?',
            'Is professional indemnity insurance addressed where applicable?',
            'Is public liability insurance addressed where applicable?',
            'Is asset insurance addressed where applicable?',
        ),
    ),
    Section(
        'VIII',
        gettext_lazy('Digital, ICT and Data Protection Review'),
        (
            'Are data ownership and control clearly allocated?',
            'Are TBC data protection requirements addressed?',
            'Are confidentiality obligations clear?',
            'Are the terms for authorised data processing specified?',
            'Are cybersecurity requirements and responsibilities specified?',
            'Are data breach reporting and response responsibilities specified?',
            'Are applicable ICT integration and interoperability requirements addressed?',
            'Are continuity and recovery arrangements addressed?',
        ),
    ),
    Section(
        'IX',
        gettext_lazy('Termination and Dispute Management'),
        (
            'Is the duration of the contract clearly defined?',
            'Are renewal and extension rules clear?',
            'Are termination rights for breach and any remedy periods specified?',
            'Is termination for convenience addressed where applicable?',
            'Are exit and handover arrangements specified?',
            'Is the return of assets addressed?',
            'Is transfer or return of data, documents and records addressed?',
            'Is the dispute resolution mechanism specified?',
            'Is the governing law clearly stated?',
            'Has Tanzanian jurisdiction been addressed where applicable?',
        ),
    ),
    Section(
        'X',
        gettext_lazy('Counterparty Due Diligence'),
        (
            'Has the counterparty’s legal existence been verified?',
            'Have owners and directors or other controllers been verified?',
            'Has financial capacity been assessed?',
            'Has technical capacity been assessed?',
            'Have relevant litigation and adverse proceedings been checked?',
            'Has applicable regulatory standing been checked?',
            'Has applicable tax standing been verified?',
        ),
    ),
    Section('XI', gettext_lazy('Risk Classification')),
    Section('XII', gettext_lazy('Approval and Legal Clearance')),
    Section(
        'XIII',
        gettext_lazy('Post-Execution Requirements'),
        (
            'Was the contract signed by the authorised parties?',
            'Has the contract reference number been assigned?',
            'Has the executed contract been entered in the contract register?',
            'Has a contract manager or responsible officer been appointed?',
            'Have monitoring and follow-up arrangements been established?',
            'Has the original signed contract been archived?',
        ),
    ),
)
SECTION_MAP = {section.code: section for section in SECTIONS}
REVIEW_SECTIONS = SECTIONS[1:11]
REVIEW_IDS = frozenset(key for section in REVIEW_SECTIONS for key, _ in section.items)
POST_IDS = frozenset(key for key, _ in SECTION_MAP['XIII'].items)
CHOICES = (('yes', gettext_lazy('Yes')), ('no', gettext_lazy('No')), ('na', gettext_lazy('N/A')))
RISK_LEVELS = (
    ('low', gettext_lazy('Low Risk')),
    ('medium', gettext_lazy('Medium Risk')),
    ('high', gettext_lazy('High Risk')),
    ('strategic', gettext_lazy('Strategic/Board-Level Risk')),
)
LEVELS = (
    ('low', gettext_lazy('Low')),
    ('medium', gettext_lazy('Medium')),
    ('high', gettext_lazy('High')),
)
RISK_FIELDS = ('category', 'description', 'likelihood', 'impact', 'mitigation', 'responsible')
RISK_LABELS = (
    gettext_lazy('Risk category'),
    gettext_lazy('Description'),
    gettext_lazy('Likelihood'),
    gettext_lazy('Impact'),
    gettext_lazy('Mitigation'),
    gettext_lazy('Responsible person'),
)


def valid_answer(value):
    return value.get('choice') == 'yes' or (
        value.get('choice') in ('no', 'na') and bool(value.get('remarks', '').strip())
    )


def review_progress(answers):
    return sum(valid_answer(answers.get(key, {})) for key in REVIEW_IDS)


def validate_complete(sheet):
    if sheet.template_revision != TEMPLATE_REVISION:
        raise ValidationError(_('This checklist revision is not available for new review.'))
    if review_progress(sheet.answers) != len(REVIEW_IDS):
        raise ValidationError(
            _('Complete all 79 checks in Parts I–X. Give a reason for every No or N/A.')
        )
    if sheet.risk_class not in dict(RISK_LEVELS) or not sheet.risks:
        raise ValidationError(
            _('Record the risk classification and at least one risk assessment in Part XI.')
        )


def english(label):
    with override('en'):
        return str(label)
