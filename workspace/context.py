"""Navigation from effective permissions. Introduced: 2026-11-18; revisions: Git."""

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from casework.notices import visible_inbox
from casework.policy import can_submit
from identity.models import RoleCode
from identity.policy import LEGAL_CAPABILITIES, STAFF_CAPABILITIES
from specialists.policy import MODULES


def workspace(request):
    if not getattr(request, 'policy', None) or not request.user.is_authenticated:
        return {'demo_mode': settings.DEMO_MODE}
    policy = request.policy
    current = request.resolver_match.url_name if request.resolver_match else ''
    staff = policy.any(STAFF_CAPABILITIES)
    legal = policy.any(LEGAL_CAPABILITIES)
    ict = policy.has('identity.manage')
    staff_surface = request.path.startswith('/portal/') or policy.landing == 'staff_dashboard'
    main, specialist_nav, secondary = [], [], []

    def add(group, name, label, icon, related=()):
        group.append(
            {'url': reverse(name), 'label': label, 'icon': icon, 'active': current in (name, *related)}
        )

    if staff_surface and staff:
        add(main, 'staff_dashboard', _('Dashboard'), 'grid')
        add(main, 'staff_requests', _('My requests'), 'files', ('staff_request', 'case_reply'))
        if can_submit(policy):
            add(main, 'new_request', _('New legal request'), 'file')
    elif legal:
        add(main, 'legal_dashboard', _('Dashboard'), 'grid')
        add(
            main,
            'matter_register',
            _('Matter register'),
            'briefcase',
            ('matter_detail', 'case_action', 'case_review'),
        )
        add(
            main,
            'contract_register',
            _('Contract register'),
            'files',
            ('contract_detail', 'contract_version', 'checklist_section'),
        )
    if legal and not staff_surface:
        selected_module = request.resolver_match.kwargs.get('module') if request.resolver_match else None
        for key, module in MODULES.items():
            specialist_nav.append(
                {
                    'url': reverse('specialist_index', args=[key]),
                    'label': module['label'],
                    'icon': module['icon'],
                    'active': current.startswith('specialist_') and key == selected_module,
                }
            )
        add(specialist_nav, 'court_calendar', _('Court calendar'), 'calendar', ('legal_deadlines',))
    if (legal and not staff_surface) or policy.has('records.department'):
        add(
            main,
            'library_index',
            _('Document repository'),
            'files',
            ('library_detail', 'library_action', 'library_create'),
        )
    if ict:
        add(main, 'ict_dashboard', _('System overview'), 'grid')
        add(
            main,
            'accounts',
            _('User accounts'),
            'users',
            ('account_detail', 'employee_edit', 'employee_new'),
        )
        add(
            main,
            'directory',
            _('Organisation directory'),
            'building',
            ('directory_new', 'directory_edit'),
        )
    if policy.has('reports.aggregate'):
        add(main, 'management_dashboard', _('Management overview'), 'chart')
    if policy.has('records.department'):
        add(main, 'records_register', _('Records index'), 'files')
    if policy.any(('access.decide', 'access.apply')) and not staff_surface:
        add(main, 'access_queue', _('Access requests'), 'key', ('access_new', 'access_detail'))
    if policy.any(('audit.read', 'audit.technical')):
        add(main, 'audit_log', _('Audit log'), 'shield')
    add(secondary, 'inbox', _('Notifications'), 'bell')
    add(secondary, 'profile', _('My profile'), 'user', ('change_password',))
    switch = None
    if staff and policy.landing != 'staff_dashboard':
        switch = {
            'url': reverse(policy.landing if staff_surface else 'staff_dashboard'),
            'label': _('Back to administration') if staff_surface else _('Open staff portal'),
        }
    workspace_label = (
        _('Staff portal')
        if staff_surface
        else _('ICT administration')
        if ict
        else _('Legal administration')
        if legal
        else _('Oversight workspace')
    )
    search = (
        'staff_requests'
        if staff_surface and staff
        else 'matter_register'
        if legal
        else 'accounts'
        if ict
        else ''
    )
    return {
        'nav_main': main,
        'nav_secondary': secondary,
        'nav_specialist': specialist_nav,
        'workspace_label': workspace_label,
        'workspace_switch': switch,
        'search_route': search,
        'today_local': timezone.localdate(),
        'can_dept': policy.has('request.department'),
        'can_submit_request': staff_surface and can_submit(policy),
        'demo_mode': request.user.is_demo,
        'actor_role': RoleCode(policy.primary_role).label if policy.primary_role else _('Employee'),
        'initials': ''.join(word[0] for word in request.user.display_name.split()[:2]).upper(),
        'notice_count': visible_inbox(policy).filter(read_at__isnull=True).count()
        if request.user.show_unread_badge
        else 0,
        'can_apply': policy.has('access.apply'),
        'can_password': settings.LOCAL_AUTH_ENABLED
        and not request.user.is_demo
        and request.user.has_usable_password(),
        'language_code': request.LANGUAGE_CODE,
        'time_zone_label': timezone.localtime().tzname(),
    }
