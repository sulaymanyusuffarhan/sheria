"""Phase 1 routes. No framework superuser console. Introduced: 2026-11-18; revisions: Git."""

from django.urls import path
from django.views.i18n import set_language

from identity import views as identity
from workspace import views as work

urlpatterns = [
    path('', identity.home, name='home'),
    path('login/', identity.sign_in, name='login'),
    path('login/demo/', identity.demo_sign_in, name='demo_login'),
    path('logout/', identity.sign_out, name='logout'),
    path('language/', set_language, name='language'),
    path('profile/', identity.profile, name='profile'),
    path('profile/password/', identity.change_password, name='change_password'),
    path('notifications/', identity.inbox, name='inbox'),
    path('notifications/read/', identity.mark_read, name='mark_all_read'),
    path('notifications/<int:pk>/read/', identity.mark_read, name='mark_read'),
    path('portal/', work.staff_dashboard, name='staff_dashboard'),
    path('portal/requests/', work.staff_requests, name='staff_requests'),
    path('portal/requests/<uuid:pk>/', work.staff_request, name='staff_request'),
    path('admin/', work.admin_home, name='legal_dashboard'),
    path('admin/matters/', work.matter_register, name='matter_register'),
    path('admin/matters/<uuid:pk>/', work.matter_detail, name='matter_detail'),
    path('admin/management/', work.management_dashboard, name='management_dashboard'),
    path('admin/records/', work.records_register, name='records_register'),
    path('admin/ict/', identity.ict_dashboard, name='ict_dashboard'),
    path('admin/directory/', identity.directory, name='directory'),
    path('admin/directory/<str:kind>/new/', identity.directory_form, name='directory_new'),
    path('admin/directory/<str:kind>/<uuid:pk>/edit/', identity.directory_form, name='directory_edit'),
    path('admin/accounts/', identity.accounts, name='accounts'),
    path('admin/accounts/new/', identity.employee_form, name='employee_new'),
    path('admin/accounts/<uuid:pk>/', identity.account_detail, name='account_detail'),
    path('admin/accounts/<uuid:pk>/edit/', identity.employee_form, name='employee_edit'),
    path('admin/accounts/<uuid:pk>/access/', identity.access_new, name='access_new'),
    path('admin/access/', identity.access_queue, name='access_queue'),
    path('admin/access/<uuid:pk>/', identity.access_detail, name='access_detail'),
    path('admin/access/<uuid:pk>/apply/', identity.access_apply, name='access_apply'),
    path('admin/access/grants/<int:pk>/revoke/', identity.access_revoke, name='access_revoke'),
    path('admin/audit/', identity.audit_log, name='audit_log'),
    path('api/v1/staff/requests/', work.api_staff_requests, name='api_staff_requests'),
    path('api/v1/legal/summary/', work.api_legal_summary, name='api_legal_summary'),
]
handler403 = work.forbidden
handler404 = work.not_found
handler500 = work.server_error
