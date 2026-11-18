"""Phase 1 identity and access screens. Introduced: 2026-11-18; revisions: Git."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from . import services
from .forms import (
    ChangePasswordForm,
    DecideAccessForm,
    DirectoryForm,
    EmployeeForm,
    ProfileForm,
    RequestAccessForm,
    SignInForm,
)
from .models import (
    AccessRequest,
    AuditLog,
    Department,
    Directorate,
    RoleCode,
    RoleGrant,
    TotpDevice,
    User,
)
from .policy import DELEGABLE_ROLES, Policy
from .security import audit, demo_available, end_session, limit_attempt, start_session, verify_totp

DEMO_CHOICES = (
    (RoleCode.HEAD, gettext_lazy('Legal dashboard'), 'briefcase'),
    (RoleCode.STAFF, gettext_lazy('Staff portal'), 'user'),
    (RoleCode.ICT, gettext_lazy('ICT console'), 'shield'),
    (RoleCode.OFFICER, RoleCode.OFFICER.label, 'briefcase'),
    (RoleCode.SENIOR, RoleCode.SENIOR.label, 'briefcase'),
    (RoleCode.HOD, RoleCode.HOD.label, 'building'),
    (RoleCode.MANAGEMENT, gettext_lazy('Management overview'), 'chart'),
    (RoleCode.AUDITOR, RoleCode.AUDITOR.label, 'shield'),
    (RoleCode.RECORDS, RoleCode.RECORDS.label, 'files'),
)


@require_safe
def home(request):
    return redirect(request.policy.landing if request.user.is_authenticated else 'login')


def _language_cookie(response, language):
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        language,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
        max_age=60 * 60 * 24 * 365,
    )
    return response


@require_http_methods(['GET', 'HEAD', 'POST'])
def sign_in(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = SignInForm(request.POST if request.method == 'POST' else None)
    show_demo = demo_available()
    next_url = request.POST.get('next', '') if request.method == 'POST' else request.GET.get('next', '')
    if not next_url.startswith('/') or not url_has_allowed_host_and_scheme(
        next_url, {request.get_host()}, require_https=request.is_secure()
    ):
        next_url = ''
    if request.method == 'POST' and settings.LOCAL_AUTH_ENABLED and form.is_valid():
        data = form.cleaned_data
        if limit_attempt(request, data['username']):
            user = authenticate(request, username=data['username'], password=data['password'])
            if user and not user.is_demo:
                mfa_required = Policy(user).requires_mfa or TotpDevice.objects.filter(user=user).exists()
                if not mfa_required or verify_totp(user, data['code']):
                    start_session(request, user, verified_mfa=mfa_required)
                    return _language_cookie(redirect(next_url or 'home'), user.language)
            audit(request, 'sign_in_failed')
            form.add_error(None, _('Sign-in failed. Check your credentials and authenticator code.'))
        else:
            form.add_error(None, _('Too many sign-in attempts. Try again later.'))
    names = (
        set(User.objects.filter(is_demo=True, is_active=True).values_list('username', flat=True))
        if show_demo
        else set()
    )
    choices = [choice for choice in DEMO_CHOICES if f'demo-{choice[0]}' in names]
    return render(
        request,
        'registration/login.html',
        {
            'form': form,
            'show_demo': show_demo,
            'next_url': next_url,
            'demo_choices': choices,
            'local_auth_enabled': settings.LOCAL_AUTH_ENABLED,
        },
    )


@require_POST
def demo_sign_in(request):
    if not demo_available():
        raise Http404
    role = request.POST.get('role', '')
    if role not in {choice[0] for choice in DEMO_CHOICES}:
        raise PermissionDenied
    if not limit_attempt(request, purpose='demo'):
        messages.error(request, _('Too many sign-in attempts. Try again later.'))
        return redirect('login')
    account = User.objects.filter(username=f'demo-{role}', is_demo=True, is_active=True).first()
    if not account or Policy(account).primary_role != role or account.has_usable_password():
        messages.error(request, _('That demonstration account is not available.'))
        return redirect('login')
    start_session(request, account, demo=True)
    return redirect('home')


@require_POST
def sign_out(request):
    end_session(request)
    return redirect('login')


@login_required
@require_http_methods(['GET', 'POST'])
def profile(request):
    form = ProfileForm(request.POST if request.method == 'POST' else None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = form.save(commit=False)
            user.save(update_fields=['language', 'show_unread_badge'])
            audit(
                request,
                'profile_updated',
                target=user.pk,
                detail={'language': user.language, 'unread_badge': user.show_unread_badge},
            )
        messages.success(request, _('Your preferences were saved.'))
        return _language_cookie(redirect('profile'), user.language)
    return render(request, 'profile.html', {'form': form})


@login_required
@require_http_methods(['GET', 'POST'])
def change_password(request):
    if request.user.is_demo or not settings.LOCAL_AUTH_ENABLED or not request.user.has_usable_password():
        raise PermissionDenied
    allowed = request.method != 'POST' or limit_attempt(
        request, request.user.username, purpose='password'
    )
    form = ChangePasswordForm(
        request.POST if request.method == 'POST' and allowed else None, user=request.user
    )
    if not allowed:
        messages.error(request, _('Too many password attempts. Try again later.'))
    if request.method == 'POST' and allowed and form.is_valid():
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if (
                not user.is_active
                or user.get_session_auth_hash() != request.user.get_session_auth_hash()
            ):
                raise PermissionDenied
            user.set_password(form.cleaned_data['new'])
            user.save(update_fields=['password'])
            services.invalidate_access(user)
            audit(request, 'password_changed', target=user.pk)
        update_session_auth_hash(request, user)
        messages.success(request, _('Your password was changed.'))
        return redirect('profile')
    return _form(
        request,
        form,
        _('Change password'),
        _('Other sessions will be signed out.'),
        'profile',
        _('Change password'),
    )


@login_required
@require_safe
def inbox(request):
    items = Paginator(request.user.notifications.all(), 12).get_page(request.GET.get('page'))
    return render(
        request,
        'inbox.html',
        {'page_obj': items, 'unread': request.user.notifications.filter(read_at__isnull=True).count()},
    )


@login_required
@require_POST
def mark_read(request, pk=None):
    qs = request.user.notifications.filter(read_at__isnull=True)
    if pk is not None:
        get_object_or_404(request.user.notifications, pk=pk)
        qs = qs.filter(pk=pk)
    with transaction.atomic():
        if count := qs.update(read_at=timezone.now()):
            audit(request, 'notifications_read', detail={'count': count})
    return redirect('inbox')


def _form(request, form, title, description, parent, button, *, extra=None):
    return render(
        request,
        'form.html',
        {
            'form': form,
            'title': title,
            'description': description,
            'back_url': reverse(parent),
            'button_label': button,
            **(extra or {}),
        },
    )


def _accounts(request):
    return User.objects.filter(is_demo=request.user.is_demo).select_related('department')


@login_required
@require_safe
def accounts(request):
    services.require(request, 'identity.manage')
    query = request.GET.get('q', '').strip()[:100]
    qs = _accounts(request).order_by('first_name', 'last_name', 'username')
    if query:
        qs = qs.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query)
        )
    return render(
        request,
        'accounts.html',
        {'page_obj': Paginator(qs, 14).get_page(request.GET.get('page')), 'q': query},
    )


@login_required
@require_http_methods(['GET', 'POST'])
def employee_form(request, pk=None):
    services.require(request, 'identity.manage')
    account = (
        get_object_or_404(_accounts(request).filter(kind=User.Kind.EMPLOYEE), pk=pk) if pk else None
    )
    form = EmployeeForm(
        request.POST if request.method == 'POST' else None, actor=request.user, account=account
    )
    if request.method == 'POST' and form.is_valid():
        try:
            if account:
                services.edit_employee(request, account, form.cleaned_data)
                messages.success(request, _('Account updated.'))
            else:
                account = services.create_employee(request, form.cleaned_data)
                messages.success(
                    request,
                    _('Account created. Local credential enrolment is a separate ICT procedure.'),
                )
            return redirect('account_detail', pk=account.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
        except IntegrityError:
            form.add_error(None, _('The account could not be saved. Check for duplicate values.'))
    return _form(
        request,
        form,
        _('Edit employee account') if account else _('Add employee account'),
        _(
            'Manage verified employee directory information. Privileged access requires separate approval.'
        ),
        'accounts',
        _('Save account'),
    )


@login_required
@require_safe
def account_detail(request, pk):
    services.require(request, 'identity.manage')
    account = get_object_or_404(_accounts(request), pk=pk)
    return render(
        request,
        'account_detail.html',
        {'account': account, 'grants': Policy(account).grants, 'delegable_roles': DELEGABLE_ROLES},
    )


@login_required
@require_safe
def directory(request):
    services.require(request, 'organisation.manage')
    dirs = Directorate.objects.filter(is_demo=request.user.is_demo).prefetch_related('departments')
    return render(request, 'directory.html', {'directorates': dirs})


@login_required
@require_http_methods(['GET', 'POST'])
def directory_form(request, kind, pk=None):
    services.require(request, 'organisation.manage')
    if kind not in ('directorate', 'department'):
        raise Http404
    model = Directorate if kind == 'directorate' else Department
    demo = request.user.is_demo
    unit = get_object_or_404(model.objects.filter(is_demo=demo), pk=pk) if pk else None
    form = DirectoryForm(
        request.POST if request.method == 'POST' else None, model=model, actor=request.user, unit=unit
    )
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                if kind == 'department':
                    parent = Directorate.objects.select_for_update().get(
                        pk=form.cleaned_data['directorate'].pk
                    )
                    if form.cleaned_data['is_active'] and not parent.is_active:
                        raise ValidationError(_('Select an active directorate.'))
                saved = (
                    model.objects.select_for_update().get(pk=unit.pk) if unit else model(is_demo=demo)
                )
                was_active = saved.is_active if unit else None
                if unit and not form.cleaned_data['is_active']:
                    children = (
                        saved.departments.filter(is_active=True)
                        if kind == 'directorate'
                        else User.objects.filter(department=saved, is_active=True)
                    )
                    if children.exists():
                        raise ValidationError(
                            _('Move or deactivate active members and sub-units first.')
                        )
                for key, value in form.cleaned_data.items():
                    setattr(saved, key, value)
                saved.save()
                audit(
                    request,
                    'unit_updated' if unit else 'unit_created',
                    area=AuditLog.Area.ORGANISATION,
                    target=saved.pk,
                    detail={'kind': kind, 'before_active': was_active, 'active': saved.is_active},
                )
            messages.success(request, _('Organisation unit saved.'))
            return redirect('directory')
        except (IntegrityError, ValidationError) as exc:
            form.add_error(
                None, exc if isinstance(exc, ValidationError) else _('That unit code is already in use.')
            )
    title = (
        (_('Edit directorate') if unit else _('Add directorate'))
        if kind == 'directorate'
        else (_('Edit department') if unit else _('Add department'))
    )
    return _form(
        request, form, title, _('Update the organisation directory.'), 'directory', _('Save unit')
    )


def _access_items(request):
    if not request.policy.any(('access.apply', 'access.decide')):
        raise PermissionDenied
    return AccessRequest.objects.filter(
        target__is_demo=request.user.is_demo, target__kind=User.Kind.EMPLOYEE
    ).select_related('target', 'role', 'department', 'requested_by', 'decided_by')


@login_required
@require_safe
def access_queue(request):
    return render(
        request,
        'access_queue.html',
        {'page_obj': Paginator(_access_items(request), 14).get_page(request.GET.get('page'))},
    )


@login_required
@require_http_methods(['GET', 'POST'])
def access_new(request, pk):
    services.require(request, 'access.apply')
    account = get_object_or_404(
        _accounts(request).filter(kind=User.Kind.EMPLOYEE, is_active=True), pk=pk
    )
    form = RequestAccessForm(request.POST if request.method == 'POST' else None, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        try:
            item = services.request_access(request, account, **form.cleaned_data)
            messages.success(request, _('Access request sent for a separate decision.'))
            return redirect('access_detail', pk=item.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return _form(
        request,
        form,
        _('Request scoped access'),
        account.display_name,
        'accounts',
        _('Send for approval'),
    )


@login_required
@require_http_methods(['GET', 'POST'])
def access_detail(request, pk):
    item = get_object_or_404(_access_items(request), pk=pk)
    can_decide = (
        request.policy.has('access.decide')
        and item.status == AccessRequest.Status.PENDING
        and request.user.pk not in (item.target_id, item.requested_by_id)
    )
    form = DecideAccessForm(request.POST if request.method == 'POST' else None) if can_decide else None
    if request.method == 'POST':
        if not can_decide:
            raise PermissionDenied
        if form.is_valid():
            try:
                services.decide_access(request, item, **form.cleaned_data)
                messages.success(request, _('Decision recorded.'))
                return redirect('access_detail', pk=pk)
            except ValidationError as exc:
                form.add_error(None, exc)
    return render(request, 'access_detail.html', {'item': item, 'form': form})


@login_required
@require_POST
def access_apply(request, pk):
    services.require(request, 'access.apply')
    item = get_object_or_404(_access_items(request), pk=pk)
    try:
        services.apply_access(request, item)
        messages.success(request, _('Access applied. Existing sessions for this account were revoked.'))
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect('access_detail', pk=pk)


@login_required
@require_POST
def access_revoke(request, pk):
    services.require(request, 'access.apply')
    grant = get_object_or_404(RoleGrant.objects.filter(user__in=_accounts(request)), pk=pk)
    try:
        services.revoke_access(request, grant)
        messages.success(request, _('Access revoked.'))
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return redirect('account_detail', pk=grant.user_id)


@login_required
@require_safe
def ict_dashboard(request):
    services.require(request, 'identity.manage')
    qs = _accounts(request)
    metrics = [
        {
            'label': _('Active accounts'),
            'value': qs.filter(is_active=True).count(),
            'icon': 'users',
            'tone': 'green',
        },
        {
            'label': _('Inactive accounts'),
            'value': qs.filter(is_active=False).count(),
            'icon': 'user',
            'tone': 'blue',
        },
        {
            'label': _('Departments'),
            'value': Department.objects.filter(is_demo=request.user.is_demo, is_active=True).count(),
            'icon': 'building',
            'tone': 'purple',
        },
        {
            'label': _('Access requests to apply'),
            'value': _access_items(request).filter(status=AccessRequest.Status.APPROVED).count(),
            'icon': 'shield',
            'tone': 'amber',
        },
    ]
    return render(
        request, 'ict_dashboard.html', {'metrics': metrics, 'recent': _access_items(request)[:5]}
    )


@login_required
@require_safe
def audit_log(request):
    if not request.policy.any(('audit.read', 'audit.technical')):
        raise PermissionDenied
    qs = AuditLog.objects.filter(is_demo=request.user.is_demo).select_related('actor')
    if request.policy.has('audit.technical') or request.GET.get('area') == 'technical':
        qs = qs.exclude(area=AuditLog.Area.LEGAL)
    return render(
        request, 'audit_log.html', {'page_obj': Paginator(qs, 16).get_page(request.GET.get('page'))}
    )
