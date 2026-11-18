"""Printable copy of the frozen issued opinion, never of a working draft."""

from io import BytesIO

from django.utils.translation import gettext as _
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from casework.pdf import _date, _esc

from .opinions import OPINION_FIELDS


def issued_pdf(issue):
    opinion = issue.review.package['opinion']
    title = _('Issued opinion')
    output = BytesIO()
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output, pagesize=A4, title=title, author='TBC Legal Services')
    content = [
        Paragraph(_esc(f'TBC Legal Services · {title}'), styles['Title']),
        Paragraph(
            _esc(f'{opinion["reference"]} · {_("Version")} {opinion["number"]}'), styles['Heading2']
        ),
        Paragraph(_esc(f'{_("Issued")}: {_date(issue.issued_at)}'), styles['BodyText']),
    ]
    if issue.version.opinion.case.matter.is_demo:
        content.append(Paragraph(_esc(_('Demonstration only · No real legal data')), styles['BodyText']))
    for field, label in zip(
        OPINION_FIELDS,
        (
            _('Question presented'),
            _('Verified facts and scope'),
            _('Authorities and citations'),
            _('Opinion analysis'),
            _('Conclusion'),
        ),
        strict=True,
    ):
        content.extend(
            [
                Spacer(1, 10),
                Paragraph(_esc(label), styles['Heading3']),
                Paragraph(_esc(opinion[field]), styles['BodyText']),
            ]
        )
    doc.build(content)
    return output.getvalue()
