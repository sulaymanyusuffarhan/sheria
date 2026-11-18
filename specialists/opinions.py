"""The opinion lane feeds the existing Phase 2 decision, not a parallel approval."""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from casework.models import Decision
from casework.policy import clean_documents, own_case
from casework.services import Conflict, digest
from matters.models import Classification

from .models import OpinionIssue, OpinionVersion

OPINION_FIELDS = ('question', 'facts', 'authorities', 'analysis', 'conclusion')


def latest_draft(case):
    return (
        OpinionVersion.objects.filter(opinion_id=case.pk)
        .select_related('authored_by', 'senior_review', 'evidence')
        .first()
    )


def freeze_opinion(case, actor, *, share=False):
    version = latest_draft(case)
    senior = getattr(version, 'senior_review', None)
    if not senior or not senior.endorsed or version.authored_by_id != actor.pk:
        raise ValidationError(_('The assigned author needs an independently endorsed opinion version.'))
    if case.reviews.filter(package__opinion__version_id=str(version.pk)).exists():
        raise ValidationError(
            _('Create and obtain Senior review of a new opinion version before resubmitting.')
        )
    if share and (case.internal or case.matter.classification != Classification.INTERNAL):
        raise ValidationError(_('The full opinion cannot be released to staff on this matter.'))
    if version.evidence_id and not clean_documents(case).filter(pk=version.evidence_id).exists():
        raise ValidationError(_('The supporting evidence is not available.'))
    return {
        'version_id': str(version.pk),
        'number': version.number,
        'reference': version.opinion.reference,
        'author_id': str(actor.pk),
        'author': actor.display_name,
        'authored_at': version.authored_at.isoformat(),
        'senior_id': str(senior.reviewer_id),
        'senior': senior.reviewer.display_name,
        'senior_at': senior.reviewed_at.isoformat(),
        'senior_reason': senior.reason,
        'evidence_id': str(version.evidence_id) if version.evidence_id else None,
        'share': bool(share),
        'recipient_id': str(case.submitted_by_id) if share else None,
        **{field: getattr(version, field) for field in OPINION_FIELDS},
    }


def check_pin(case, review):
    pinned = review.package['opinion']
    version = latest_draft(case)
    if (
        not version
        or str(version.pk) != pinned['version_id']
        or (str(version.evidence_id) if version.evidence_id else None) != pinned['evidence_id']
        or any(getattr(version, key) != pinned[key] for key in OPINION_FIELDS)
    ):
        raise Conflict()
    return version


def issue_opinion(case, actor, review):
    version = check_pin(case, review)
    if review.decision.outcome != Decision.Outcome.CLEAR:
        raise Conflict()
    share = review.package['opinion']['share']
    if share and (
        case.internal
        or case.matter.classification != Classification.INTERNAL
        or review.package['opinion'].get('recipient_id') != str(case.submitted_by_id)
    ):
        raise ValidationError(
            _('The approved audience is no longer eligible. Do not issue this version.')
        )
    return OpinionIssue.objects.create(
        version=version,
        review=review,
        issued_by=actor,
        staff_recipient_id=case.submitted_by_id if share else None,
    )


def issued_for_staff(policy, case):
    if not own_case(policy, case) or case.matter.classification != Classification.INTERNAL:
        return None
    issue = issue_records(case).filter(staff_recipient=policy.user).first()
    if (
        issue
        and issue.review.package['opinion'].get('recipient_id') == str(policy.user.pk)
        and valid_issue(case, issue)
    ):
        return issue
    return None


def issue_records(case):
    return OpinionIssue.objects.filter(
        version__opinion_id=case.pk, review__case=case, review__decision__outcome=Decision.Outcome.CLEAR
    ).select_related('version__opinion__case__matter', 'review')


def valid_issue(case, issue):
    frozen = issue.review.package.get('opinion', {})
    return (
        issue.version.opinion_id == case.pk
        and frozen.get('version_id') == str(issue.version_id)
        and issue.review.digest == digest(issue.review.package)
    )
