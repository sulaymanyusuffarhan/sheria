"""Live library access; metadata audience and byte audience are always separate."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_safe

from casework.models import Document
from casework.policy import cases_for
from casework.storage import read_blob
from casework.views import _apply, _attachment, _form, record_access
from identity.policy import LEGAL_CAPABILITIES
from specialists.policy import may_operate

from . import forms, services
from .models import Item


def _access(request):
    if not (request.policy.any(LEGAL_CAPABILITIES) or request.policy.has('records.department')):
        raise PermissionDenied


def _item(request, pk):
    _access(request)
    item = services.items_for_legal(request.policy).filter(pk=pk).select_related('case__matter').first()
    if item:
        return item, True
    return get_object_or_404(
        services.items_for_records(request.policy).select_related('case__matter'), pk=pk
    ), False


@login_required
@require_safe
def index(request):
    _access(request)
    policy = request.policy
    evidence = request.GET.get('view') == 'evidence'
    if evidence and not policy.any(LEGAL_CAPABILITIES):
        raise PermissionDenied
    term = request.GET.get('q', '').strip()[:120]
    category = request.GET.get('category', '')
    scope_case = request.GET.get('matter')
    if scope_case:
        # Selecting a matter is a scoped read, not permission to discover unrelated references.
        from uuid import UUID

        try:
            case_id = UUID(scope_case)
        except ValueError as exc:
            raise Http404 from exc
        qs = cases_for(policy) if evidence else (cases_for(policy) | services.records_cases(policy))
        case = get_object_or_404(qs, pk=case_id)
    else:
        case = None
    record_access(request, 'library_search_viewed', '')
    if evidence:
        rows = services.matter_files(policy).select_related('case__matter')
        if term:
            rows = rows.filter(Q(name__icontains=term) | Q(case__matter__reference__icontains=term))
    else:
        legal = services.items_for_legal(policy)
        metadata = services.items_for_records(policy)
        if term:
            legal = legal.filter(
                Q(reference__icontains=term)
                | Q(title__icontains=term)
                | Q(tags__icontains=term)
                | Q(case__matter__reference__icontains=term)
                | Q(versions__document__name__icontains=term)
            )
            metadata = metadata.filter(
                Q(reference__icontains=term) | Q(case__matter__reference__icontains=term)
            )
        rows = (legal | metadata).distinct().select_related('case__matter')
        if category in Item.Category.values:
            rows = rows.filter(category=category)
    if case:
        rows = rows.filter(case_id=case.pk)
    page = Paginator(rows, 15).get_page(request.GET.get('page'))
    if not evidence:
        visible = set(
            services.items_for_legal(policy)
            .filter(pk__in=[item.pk for item in page])
            .values_list('pk', flat=True)
        )
        for item in page:
            item.legal = item.pk in visible
    return render(
        request,
        'library/index.html',
        {
            'page_obj': page,
            'q': term,
            'category': category,
            'categories': Item.Category.choices,
            'case': case,
            'evidence': evidence,
            'legal_library': policy.any(LEGAL_CAPABILITIES),
            'new_item': bool(
                case and cases_for(policy).filter(pk=case.pk).exists() and may_operate(policy, case)
            ),
        },
    )


@login_required
@require_safe
def detail(request, pk):
    item, is_legal = _item(request, pk)
    record_access(request, 'library_item_viewed', item.pk)
    return render(
        request,
        'library/detail.html',
        {
            'item': item,
            'legal': is_legal,
            'versions': item.versions.select_related('document__uploaded_by') if is_legal else None,
            'custody': item.custody.select_related('actor'),
            'actions': [
                (cmd, label)
                for cmd, label in services.COMMANDS.items()
                if services.allowed(request.policy, item, cmd)
            ],
        },
    )


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def create(request, matter):
    if not request.policy.any(LEGAL_CAPABILITIES):
        raise PermissionDenied
    case = get_object_or_404(cases_for(request.policy), pk=matter)
    if not may_operate(request.policy, case):
        raise PermissionDenied
    posted = request.method == 'POST'
    form = forms.ItemForm(
        request.POST if posted else None,
        request.FILES if posted else None,
        case=case,
        policy=request.policy,
    )
    status = 400 if posted else 200
    if posted and form.is_valid():
        status, result = _apply(form, lambda: services.create(request, case.pk, form.cleaned_data))
        if result:
            messages.success(request, _('Document registered in the legal repository.'))
            return redirect('library_detail', result.pk)
    return _form(
        request,
        form,
        title=_('Register repository document'),
        description=_('The file and all future versions stay private to the authorised audience.'),
        back=reverse('matter_detail', args=[case.pk]),
        button=_('Register document'),
        status=status,
        context={'case': case},
    )


@login_required
@require_http_methods(['GET', 'HEAD', 'POST'])
def action(request, pk, command):
    item, legal = _item(request, pk)
    if command not in services.COMMANDS:
        raise Http404
    if not services.allowed(request.policy, item, command):
        raise PermissionDenied
    posted = request.method == 'POST'
    types = {'version': forms.VersionForm, 'rescan': forms.RescanForm}
    form = types.get(command, forms.CustodyForm)(
        request.POST if posted else None,
        request.FILES if posted else None,
        case=item.case,
        **({'item': item} if command == 'rescan' else {}),
    )
    status = 400 if posted else 200
    if posted and form.is_valid():
        status, saved = _apply(form, lambda: services.execute(request, pk, command, form.cleaned_data))
        if saved:
            messages.success(request, _('Repository action recorded.'))
            return redirect('library_detail', pk)
    label = services.COMMANDS[command]
    description = _('Custody actions never delete a file or expand its audience.')
    return _form(
        request,
        form,
        title=label,
        description=description,
        back=reverse('library_detail', args=[pk]),
        button=label,
        status=status,
        context={'case': item.case} if legal else None,
    )


@login_required
@require_safe
def download(request, pk, version_id):
    if not request.policy.any(LEGAL_CAPABILITIES):
        raise PermissionDenied
    item = get_object_or_404(services.items_for_legal(request.policy), pk=pk)
    row = get_object_or_404(
        item.versions.select_related('document__blob'),
        pk=version_id,
        document__case_id=item.case_id,
        document__scan=Document.Scan.CLEAN,
        document__kind=Document.Kind.REPOSITORY,
    )
    try:
        content = read_blob(row.document)
    except ValidationError:
        record_access(request, 'private_document_unavailable', row.document_id)
        return render(request, 'casework/unavailable.html', status=503)
    record_access(request, 'library_version_downloaded', row.pk)
    return _attachment(content, row.document.name, row.document.media_type)
