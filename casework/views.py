"""Phase 2 surfaces. All writes are CSRF-protected, revision-bound named commands. Revisions: Git."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError
from django.db.models import OuterRef, Q, Subquery
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_http_methods, require_safe

from identity.models import AuditLog
from identity.policy import LEGAL_CAPABILITIES
from identity.security import audit
from matters.models import Status

from . import checklist, forms, services
from .models import (
    Case,
    Checklist,
    Contract,
    ContractType,
    ContractVersion,
    Document,
)
from .pdf import checklist_pdf
from .policy import (
    ACTIONS,
    CONTRACT_CATEGORIES,
    assignee,
    can_act,
    can_submit,
    cases_for,
    current_assignment,
    head,
    own_case,
    staff_documents,
)

ACTION_HELP = {
    'triage': gettext_lazy('Start checking the department, request details and required evidence.'),
    'accept': gettext_lazy(
        'Confirm the evidence is complete. Contract requests are registered with their first draft.'
    ),
    'reject_intake': gettext_lazy(
        'Record the internal reason and a separate message that the requester can see.'
    ),
    'ask': gettext_lazy(
        'The question is published to the requester. Your reason stays in the legal workspace.'
    ),
    'verify': gettext_lazy('Confirm that the reply answers the question before resuming review.'),
    'assign': gettext_lazy(
        'Choose an eligible legal officer and set a review deadline. Earlier assignments are retained.'
    ),
    'begin': gettext_lazy('Record that you have started the legal review.'),
    'note': gettext_lazy(
        'Keep this note in the legal activity history. It is never published to staff.'
    ),
    'upload': gettext_lazy(
        'Documents are private. A proposed deliverable is not visible to staff until approved and issued.'
    ),
    'rescan': gettext_lazy('Only a verified scanner result can make a quarantined document available.'),
    'version': gettext_lazy(
        'Store a new draft with a new checklist. No earlier clearance carries forward.'
    ),
    'submit_review': gettext_lazy(
        'Freeze this recommendation, contract version and evidence. The requester will not see the package.'
    ),
    'decide': gettext_lazy(
        'You are deciding on this frozen package only. Legal clearance is not permission to sign.'
    ),
    'issue': gettext_lazy(
        'The requester will receive the exact approved message and selected files below.'
    ),
    'reopen': gettext_lazy(
        'A change needs a new contract version and Head decision. Any earlier issued response remains in history.'
    ),
    'execute': gettext_lazy(
        'Record an external signing that already happened. This action does not sign the contract.'
    ),
    'close': gettext_lazy(
        'Close the request without changing its recorded outcome or disposing of evidence.'
    ),
}
PRIMARY = {
    Status.SUBMITTED: 'triage',
    Status.PRELIMINARY: 'accept',
    Status.INFORMATION: 'verify',
    Status.ACCEPTED: 'assign',
    Status.ASSIGNED: 'begin',
    Status.REVIEW: 'submit_review',
    Status.HEAD_REVIEW: 'decide',
    Status.APPROVED: 'issue',
    Status.COMPLETED: 'close',
    Status.REJECTED: 'close',
}


def _legal(request, pk):
    if not request.policy.any(LEGAL_CAPABILITIES):
        raise PermissionDenied
    return get_object_or_404(cases_for(request.policy), pk=pk)


def _staff(request, pk):
    if not request.policy.has('request.own'):
        raise PermissionDenied
    case = get_object_or_404(
        Case.objects.filter(submitted_by=request.user).select_related('matter'), pk=pk
    )
    if not own_case(request.policy, case):
        raise Http404
    return case


def record_access(request, action, target):
    audit(request, action, area=AuditLog.Area.LEGAL, target=target)


def _apply(form, fn):
    try:
        return 200, fn()
    except services.Conflict as exc:
        form.add_error(None, exc)
        return 409, None
    except ValidationError as exc:
        form.add_error(None, exc)
        return 400, None
    except OperationalError as exc:
        # SQLite preview can report a competing writer instead of waiting out a read lock.
        if 'database is locked' not in str(exc) and 'database table is locked' not in str(exc):
            raise
        form.add_error(None, services.Conflict())
        return 409, None


def legal_panel(request, matter):
    case = Case.objects.filter(pk=matter.pk).first()
    if not case:
        return {}
    case.matter = matter
    version = services.latest_version(case)
    assignment, review, info = (
        current_assignment(case),
        services.latest_package(case),
        services.active_information(case),
    )
    urls = []
    for key, (label, icon) in ACTIONS.items():
        if can_act(request.policy, case, key, assignment, review, info):
            target = (
                reverse('case_review', args=[case.pk, review.pk])
                if key == 'decide'
                else reverse('case_action', args=[case.pk, key])
            )
            urls.append({'url': target, 'label': label, 'icon': icon, 'name': key})
    primary = next((entry for entry in urls if entry['name'] == PRIMARY.get(matter.status)), None)
    events = Paginator(case.events.select_related('actor'), 8).get_page(request.GET.get('activity'))
    for event in events:
        event.label = ACTIONS.get(
            event.command,
            (
                {
                    'submit': _('Submit request'),
                    'reply': _('Requester replied'),
                    'save_checklist': _('Save checklist section'),
                }.get(event.command, event.command),
                '',
            ),
        )[0]
    return {
        'active_case': case,
        'case_actions': [entry for entry in urls if entry != primary],
        'primary_action': primary,
        'case_events': events,
        'current_version': version,
        'latest_review': review,
        'case_reviews': case.reviews.select_related('decision', 'version', 'reviewer')[:12],
        'case_documents': case.documents.all(),
        'information': info,
        'past_information': case.information_rounds.filter(verified_at__isnull=False).select_related(
            'verified_by'
        )[:5],
    }


def staff_panel(request, record, department):
    if department:
        return {}
    case = Case.objects.filter(pk=record.pk, submitted_by=request.user).first()
    if not case:
        return {}
    info = services.active_information(case)
    return {
        'staff_flow': True,
        'staff_documents': staff_documents(request.policy, case),
        'staff_question': info if record.public_status == 'information' else None,
    }


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def new_request(request):
    if not can_submit(request.policy):
        raise PermissionDenied
    posted = request.method == 'POST'
    form = forms.SubmitForm(
        request.POST if posted else None,
        request.FILES if posted else None,
        actor=request.user,
        language=request.LANGUAGE_CODE,
    )
    status = 400 if posted else 200
    if posted and form.is_valid():
        status, case = _apply(form, lambda: services.submit(request, form.cleaned_data))
        if case:
            messages.success(request, _('Request submitted. Keep the reference for tracking.'))
            return redirect('staff_request', case.pk)
    return render(request, 'casework/submit.html', {'form': form}, status=status)


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def reply(request, pk):
    case = _staff(request, pk)
    info = services.active_information(case)
    if case.matter.status != Status.INFORMATION or not info or info.replied_at:
        if request.method == 'POST' and _stale(request, case):
            return _stale_response(request, case, staff=True)
        raise PermissionDenied
    posted = request.method == 'POST'
    form = forms.ReplyForm(
        request.POST if posted else None,
        request.FILES if posted else None,
        case=case,
        replace_draft=info.resume_status == Status.PRELIMINARY
        and case.matter.category_id in CONTRACT_CATEGORIES,
    )
    status = 400 if posted else 200
    if posted and form.is_valid():

        def save():
            services.reply(request, pk, form.cleaned_data)
            return True

        status, saved = _apply(form, save)
        if saved:
            messages.success(request, _('Response sent for verification.'))
            return redirect('staff_request', pk)
    return _form(
        request,
        form,
        title=_('Respond to Legal Services'),
        description=info.question,
        back=reverse('staff_request', args=[pk]),
        status=status,
        button=_('Send response'),
        context={'staff_date': info.due_on},
    )


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def action(request, pk, command):
    case = _legal(request, pk)
    if command not in ACTIONS:
        raise Http404
    assignment, review, info = (
        current_assignment(case),
        services.latest_package(case),
        services.active_information(case),
    )
    if not can_act(request.policy, case, command, assignment, review, info):
        # A stale form may have become ineligible since the decision/view was rendered.
        if request.method == 'POST' and _stale(request, case):
            return _stale_response(request, case)
        raise PermissionDenied
    posted = request.method == 'POST'
    kwargs = {'case': case}
    prior = None
    if command == 'assign':
        kwargs['policy'] = request.policy
    elif command in ('version', 'submit_review'):
        kwargs['version'] = services.latest_version(case)
        if command == 'submit_review':
            prior = services.condition_round(case)
            kwargs['prior_condition'] = prior
    elif command in ('decide', 'issue', 'execute'):
        kwargs['review'] = review
        if command != 'issue':
            kwargs['actor'] = request.user
    form = forms.COMMAND_FORMS[command](
        request.POST if posted else None, request.FILES if posted else None, **kwargs
    )
    status = 400 if posted else 200
    if posted and form.is_valid():
        status, saved = _apply(form, lambda: services.run(request, pk, command, form.cleaned_data))
        if saved:
            messages.success(
                request,
                {
                    'submit_review': _('Review frozen and sent to the Head of Legal Services.'),
                    'decide': _('Head decision recorded.'),
                    'issue': _('Approved response issued to the requester.'),
                    'execute': _('External execution recorded. Part XIII is now available.'),
                    'version': _('New contract version registered.'),
                }.get(command, _('Workflow action saved.')),
            )
            if command in ('version', 'execute'):
                return redirect('contract_detail', pk)
            if command == 'decide':
                return redirect('case_review', pk, review.pk)
            return redirect('matter_detail', pk)
    return _form(
        request,
        form,
        title=ACTIONS[command][0],
        description=ACTION_HELP[command],
        back=reverse('case_review', args=[pk, review.pk])
        if command == 'decide'
        else reverse('matter_detail', args=[pk]),
        status=status,
        button=ACTIONS[command][0],
        context={
            'case': case,
            'command': command,
            'review': review if command in ('decide', 'issue', 'execute') else None,
            'release_names': [
                file['name']
                for file in review.package['evidence']
                if file['id'] in review.package['deliverables']
            ]
            if command == 'issue'
            else [],
            'condition': prior.decision if prior else None,
            'info': info if command == 'verify' else None,
        },
    )


def _stale(request, case):
    value = request.POST.get('revision') or ''
    return value.isascii() and value.isdecimal() and len(value) <= 10 and int(value) != case.revision


def _stale_response(request, case, *, staff=False):
    return render(
        request,
        'casework/conflict.html',
        {
            'case': case,
            'back_url': reverse('staff_request' if staff else 'matter_detail', args=[case.pk]),
        },
        status=409,
    )


def _form(request, form, *, title, description, back, button, status, context=None):
    return render(
        request,
        'casework/action.html',
        {
            'form': form,
            'title': title,
            'description': description,
            'back_url': back,
            'button_label': button,
            **(context or {}),
        },
        status=status,
    )


@login_required
@require_safe
def register(request):
    if not request.policy.any(LEGAL_CAPABILITIES):
        raise PermissionDenied
    query = request.GET.get('q', '').strip()[:120]
    view = request.GET.get('view', '')
    qs = Contract.objects.filter(
        case_id__in=cases_for(request.policy).order_by().values('pk')
    ).select_related('case__matter__department')
    versions = ContractVersion.objects.filter(contract_id=OuterRef('pk')).order_by('-number')
    qs = qs.annotate(
        version_number=Subquery(versions.values('number')[:1]),
        version_title=Subquery(versions.values('title')[:1]),
        counterparty=Subquery(versions.values('counterparty')[:1]),
        signed=Subquery(versions.values('execution__signed_on')[:1]),
    )
    if query:
        qs = qs.filter(
            Q(reference__icontains=query)
            | Q(version_title__icontains=query)
            | Q(counterparty__icontains=query)
        )
    if view == 'executed':
        qs = qs.filter(signed__isnull=False)
    elif view == 'review':
        qs = qs.filter(
            case__matter__status__in=(
                Status.ASSIGNED,
                Status.REVIEW,
                Status.HEAD_REVIEW,
                Status.INFORMATION,
            )
        )
    records = Paginator(qs.order_by('-registered_at', '-pk'), 14).get_page(request.GET.get('page'))
    record_access(request, 'contract_register_viewed', '')
    return render(
        request, 'casework/register.html', {'page_obj': records, 'q': query, 'view_filter': view}
    )


def _contract(case, version_id=None):
    contract = get_object_or_404(Contract, pk=case.pk)
    versions = contract.versions.select_related(
        'contract', 'draft', 'execution', 'execution__signed_copy'
    )
    version = get_object_or_404(versions, pk=version_id) if version_id else versions.first()
    if not version:
        raise Http404
    return contract, version, versions


def _part_allowed(policy, case, version, code, current):
    if policy.user.pk == case.submitted_by_id:
        return False
    mine = assignee(policy, current_assignment(case))
    if code == 'XIII':
        return (
            (mine or head(policy))
            and hasattr(version, 'execution')
            and not version.checklists.filter(
                kind=Checklist.Kind.POST, finalized_at__isnull=False
            ).exists()
        )
    return current and mine and case.matter.status == Status.REVIEW


def _identity_rows(values):
    return [
        (
            label,
            dict(ContractType.choices).get(values.get(key), values.get(key))
            if key == 'contract_type'
            else values.get(key),
        )
        for key, label in checklist.IDENTITY_FIELDS
    ]


def _render_parts(saved_sections, answers, docs):
    answers_labels = dict(checklist.CHOICES)
    return [
        {
            **section,
            'items': [
                {
                    'key': key,
                    'prompt': prompt,
                    'label': answers_labels.get(answers.get(key, {}).get('choice'), _('Unanswered')),
                    'value': answers.get(key, {}),
                    'evidence': [
                        docs[id_] for id_ in answers.get(key, {}).get('evidence', []) if id_ in docs
                    ],
                }
                for key, prompt in section['items']
            ],
        }
        for section in saved_sections
    ]


@login_required
@require_safe
def contract(request, pk, version_id=None):
    case = _legal(request, pk)
    record, version, versions = _contract(case, version_id)
    active = services.latest_version(case).pk == version.pk
    pre = version.checklists.get(kind=Checklist.Kind.PRE)
    post = version.checklists.filter(kind=Checklist.Kind.POST).first()
    execution = getattr(version, 'execution', None)
    review = version.reviews.select_related('decision').first()
    can_edit = _part_allowed(request.policy, case, version, 'I', active)
    can_post = post and _part_allowed(request.policy, case, version, 'XIII', active)
    parts = []
    for part in checklist.REVIEW_SECTIONS:
        complete = sum(checklist.valid_answer(pre.answers.get(key, {})) for key, _ in part.items)
        parts.append({'part': part, 'complete': complete, 'count': len(part.items)})
    identity = (
        review.package['contract']['identity']
        if review and not can_edit
        else services.identification(case, version, current_assignment(case))
    )
    record_access(request, 'contract_version_viewed', version.pk)
    return render(
        request,
        'casework/contract.html',
        {
            'case': case,
            'contract': record,
            'version': version,
            'past_versions': versions.exclude(pk=version.pk),
            'active_version': active,
            'superseded': version.number < case.minimum_version,
            'parts': parts,
            'can_edit': can_edit,
            'can_post': can_post,
            'post': post,
            'execution': execution,
            'identity': _identity_rows(identity),
            'risk_label': dict(checklist.RISK_LEVELS).get(pre.risk_class),
            'review': review,
            'answered': checklist.review_progress(pre.answers),
            'checks': len(checklist.REVIEW_IDS),
            'form_reference': checklist.FORM_REFERENCE,
            'form_version': pre.template_revision,
            'post_count': sum(
                checklist.valid_answer(post.answers.get(key, {})) for key in checklist.POST_IDS
            )
            if post
            else 0,
            'post_parts': _render_parts(
                [{'items': checklist.SECTION_MAP['XIII'].items}],
                post.answers,
                {str(doc.pk): {'name': doc.name} for doc in case.documents.all()},
            )
            if post
            else [],
        },
    )


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def section(request, pk, version_id, code):
    case = _legal(request, pk)
    record, version, _versions = _contract(case, version_id)
    part = checklist.SECTION_MAP.get(code)
    if not part or code in ('A', 'XII'):
        raise Http404
    if not _part_allowed(
        request.policy, case, version, code, services.latest_version(case).pk == version.pk
    ):
        if request.method == 'POST' and _stale(request, case):
            return _stale_response(request, case)
        raise PermissionDenied
    sheet = version.checklists.get(kind=Checklist.Kind.POST if code == 'XIII' else Checklist.Kind.PRE)
    posted = request.method == 'POST'
    form = forms.SectionForm(request.POST if posted else None, case=case, section=part, sheet=sheet)
    status = 400 if posted else 200
    if posted and form.is_valid():
        status, saved = _apply(
            form, lambda: services.save_checklist(request, pk, version.pk, code, form.cleaned_data)
        )
        if saved:
            messages.success(request, _('Checklist section saved.'))
            return redirect(reverse('contract_version', args=[pk, version.pk]) + f'#part-{code}')
    return render(
        request,
        'casework/section.html',
        {
            'case': case,
            'contract': record,
            'version': version,
            'part': part,
            'form': form,
            'form_reference': checklist.FORM_REFERENCE,
            'template_revision': sheet.template_revision,
        },
        status=status,
    )


@login_required
@require_safe
def review(request, pk, review_id):
    case = _legal(request, pk)
    package = get_object_or_404(
        case.reviews.select_related('version', 'decision', 'decision__condition_owner'), pk=review_id
    )
    frozen = package.package
    doc_map = {file['id']: file for file in frozen['evidence']}
    values = frozen.get('contract')
    parts = _render_parts(values['sections'][1:11], values['answers'], doc_map) if values else []
    decision_allowed = can_act(request.policy, case, 'decide', current_assignment(case), package)
    record_access(request, 'frozen_review_viewed', package.pk)
    return render(
        request,
        'casework/review.html',
        {
            'case': case,
            'review': package,
            'snapshot': frozen,
            'contract': values,
            'identity': _identity_rows(values['identity']) if values else [],
            'parts': parts,
            'risk_label': dict(checklist.RISK_LEVELS).get(values['risk_class']) if values else None,
            'may_decide': decision_allowed,
            'release_files': [doc_map[key] for key in frozen['deliverables']],
        },
    )


def _attachment(data, filename, content_type):
    response = HttpResponse(data, content_type=content_type)
    response.headers['Content-Disposition'] = content_disposition_header(True, filename)
    response.headers['Content-Length'] = str(len(data))
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    return response


@login_required
@require_safe
def export(request, pk, review_id):
    case = _legal(request, pk)
    package = get_object_or_404(
        case.reviews.select_related('version', 'decision'), pk=review_id, version__isnull=False
    )
    record_access(request, 'contract_checklist_exported', package.pk)
    return _attachment(
        checklist_pdf(case, package),
        f'tbc-contract-v{package.version.number}-review-{package.number}.pdf',
        'application/pdf',
    )


@login_required
@require_safe
def download(request, pk, document_id, staff=False):
    case = _staff(request, pk) if staff else _legal(request, pk)
    docs = (
        staff_documents(request.policy, case)
        if staff
        else case.documents.filter(scan=Document.Scan.CLEAN)
    )
    doc = get_object_or_404(docs.select_related('blob'), pk=document_id)
    from .storage import read_blob

    try:
        data = read_blob(doc)
    except ValidationError:
        record_access(request, 'private_document_unavailable', doc.pk)
        return render(request, 'casework/unavailable.html', status=503)
    record_access(
        request, 'staff_document_downloaded' if staff else 'private_document_downloaded', doc.pk
    )
    return _attachment(data, doc.name, doc.media_type)
