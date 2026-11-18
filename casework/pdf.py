"""Internal, attachment-only export of a named frozen review. Revisions: Git."""

import re
from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone, translation
from django.utils.dateparse import parse_datetime
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

from . import checklist
from .models import Checklist, ContractType, Recommendation

NAVY = colors.HexColor('#12284b')
BLUE = colors.HexColor('#1e4d93')
GREY = colors.HexColor('#485c76')


def _esc(value):
    clean = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', str(value if value not in (None, '') else '—'))
    return escape(clean).replace('\n', '<br/>')


def _date(value):
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M %Z') if value else '—'


def checklist_pdf(case, review):
    contract = review.package['contract']
    output = BytesIO()
    title = 'TBC Contract Review Checklist'
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=42,
        rightMargin=42,
        topMargin=56,
        bottomMargin=47,
        title=title,
        author='TBC · Directorate of Legal Services',
    )
    sheet = getSampleStyleSheet()
    body = ParagraphStyle('TBCBody', parent=sheet['BodyText'], fontSize=10, leading=14, textColor=NAVY)
    small = ParagraphStyle('TBCMeta', parent=body, fontSize=8, leading=11, textColor=GREY)
    center = ParagraphStyle(
        'TBCCover', parent=sheet['Title'], fontSize=23, leading=27, textColor=NAVY, alignment=TA_CENTER
    )
    heading = ParagraphStyle('TBCSection', parent=sheet['Heading2'], spaceBefore=13, textColor=BLUE)
    content = []

    def line(label, value, style=body):
        content.append(Paragraph(f'<b>{_esc(label)}:</b> {_esc(value)}', style))

    def header(label):
        content.append(Paragraph(_esc(label), heading))

    def decorate(canvas, template):
        width, height = A4
        canvas.setFillColor(NAVY)
        canvas.rect(0, height - 7, width, 7, stroke=0, fill=1)
        canvas.setFont('Helvetica-Bold', 9)
        canvas.drawString(42, height - 34, 'TBC  /  DIRECTORATE OF LEGAL SERVICES')
        canvas.setFillColor(GREY)
        canvas.setFont('Helvetica', 8)
        canvas.drawString(42, 25, f'{contract["form"]}   |   Internal Use Only   |   Draft template')
        canvas.drawRightString(width - 42, 25, str(template.page))

    with translation.override('en'):
        content.append(Paragraph(title, center))
        line('Form reference', f'{contract["form"]} / template revision {contract["template_revision"]}')
        line('Classification', f'Internal Use Only · {case.matter.get_classification_display()}')
        line(
            'Source status', 'Draft digital transcription. Controlled wording requires DLS verification.'
        )
        if case.matter.is_demo:
            line(
                'Demonstration', 'Fictional data. Not a legal record, authenticator check or signature.'
            )
        line('Request', review.package['request'])
        line(
            'Contract',
            f'{contract["identity"]["reference"]} / version {contract["version_number"]} / review {review.number}',
        )
        line('Frozen package SHA-256', review.digest, small)
        line('Exact draft', contract['draft']['name'])
        line('Draft SHA-256', contract['draft']['sha256'], small)
        identity = contract['identity']
        sections = {section['code']: section for section in contract['sections']}
        header(f'A. {sections["A"]["title"]}')
        for key, label in checklist.IDENTITY_FIELDS:
            value = identity.get(key)
            line(
                label, dict(ContractType.choices).get(value, value) if key == 'contract_type' else value
            )

        def checks(section_, answers, available):
            for key, prompt in section_['items']:
                item = answers.get(key, {})
                value = dict(checklist.CHOICES).get(item.get('choice'), 'Unanswered')
                rows = [Paragraph(f'<b>{_esc(key)} · {_esc(prompt)}</b>', body)]
                rows.append(Paragraph(f'<b>{_esc(value)}</b> · {_esc(item.get("remarks"))}', small))
                if file_ids := item.get('evidence'):
                    rows.append(
                        Paragraph(
                            f'Evidence: {_esc(", ".join(available.get(id_, id_) for id_ in file_ids))}',
                            small,
                        )
                    )
                if item.get('at'):
                    rows.append(
                        Paragraph(
                            f'{_esc(item.get("by"))} · {_esc(_date(parse_datetime(item["at"])))}', small
                        )
                    )
                content.append(KeepTogether([*rows, Spacer(1, 5)]))

        evidence = {entry['id']: entry['name'] for entry in review.package['evidence']}
        for section_ in contract['sections'][1:11]:
            header(f'{section_["code"]}. {section_["title"]}')
            checks(section_, contract['answers'], evidence)
        header(f'XI. {sections["XI"]["title"]}')
        line('Risk classification', dict(checklist.RISK_LEVELS).get(contract['risk_class']))
        for index, risk in enumerate(contract['risks'], 1):
            content.append(Paragraph(f'<b>Risk {index}</b>', body))
            for key, label in zip(checklist.RISK_FIELDS, checklist.RISK_LABELS, strict=True):
                line(label, dict(checklist.LEVELS).get(risk[key], risk[key]), small)
        header(f'XII. {sections["XII"]["title"]}')
        line('Legal officer', review.package['reviewer_name'])
        line(
            'Recommendation authenticated and submitted',
            f'{_date(review.submitted_at)} · {review.package.get("reviewer_assurance", "not recorded")}',
        )
        line('Recommendation', dict(Recommendation.choices).get(review.recommendation))
        line('Rationale', review.package['reason'])
        line('Required amendments', review.package.get('amendments'))
        if review.package.get('condition_verification'):
            line('Verification of earlier conditions', review.package['condition_verification'])
        line('Proposed staff response', review.package['response'])
        decision = getattr(review, 'decision', None)
        if decision:
            line('Head of Legal Services', decision.decided_name)
            line(
                'Decision and authentication',
                f'{decision.get_outcome_display()} · {decision.assurance} · {_date(decision.decided_at)}',
            )
            line('Decision reason', decision.reason)
            if decision.conditions:
                line('Conditions (not a clearance)', decision.conditions)
                line(
                    'Condition owner / due date',
                    f'{decision.condition_owner.display_name} / {decision.condition_due:%d/%m/%Y}',
                )
            if decision.gate_evidence_id:
                line('Escalation evidence', evidence.get(str(decision.gate_evidence_id)))
        else:
            line('Head decision', 'Not recorded. This package does not constitute legal clearance.')
        line('Execution authority', 'Legal clearance is not contract execution.')
        if review.version.number < case.minimum_version:
            line(
                'Historical decision',
                'A newer version review is required. This decision is retained for history, not current clearance.',
            )
        header(f'XIII. {sections["XIII"]["title"]}')
        execution = getattr(decision, 'execution', None) if decision else None
        post = review.version.checklists.filter(kind=Checklist.Kind.POST).first() if execution else None
        if execution:
            line('Verified external signatories', execution.signatories)
            line(
                'Dates',
                f'Signed {execution.signed_on:%d/%m/%Y} · effective {execution.effective_on:%d/%m/%Y} · expiry {execution.expires_on or "not specified"}',
            )
            line('Signed counterpart', execution.signed_copy.name)
            line('Signed counterpart SHA-256', execution.signed_copy.checksum, small)
            line('Authority and prerequisite references', execution.prerequisites)
            line(
                'Recorded externally',
                f'{execution.recorded_by.display_name} · {_date(execution.recorded_at)}',
            )
        if not execution:
            line('Status', 'Execution not recorded. All six post-execution checks are unanswered.')
        elif not post.finalized_at:
            line('Status', 'Post-execution review incomplete.')
        else:
            line('Finalised', f'{post.finalized_by.display_name} · {_date(post.finalized_at)}')
        if post:
            for key, label in (
                ('manager', 'Authorised contract manager'),
                ('monitoring', 'Monitoring and follow-up plan'),
                ('archive', 'Archive reference'),
            ):
                line(label, post.post_details.get(key))
            evidence |= {str(file.pk): file.name for file in case.documents.all()}
        checks(sections['XIII'], post.answers if post else {}, evidence)
        doc.build(content, onFirstPage=decorate, onLaterPages=decorate)
    return output.getvalue()
