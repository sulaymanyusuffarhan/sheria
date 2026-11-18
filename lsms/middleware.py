"""Request-scoped authorisation and security headers. Introduced: 2026-11-18; revisions: Git."""

import uuid

from django.conf import settings
from django.utils import timezone, translation

from identity.policy import Policy
from identity.security import demo_available, end_session


class WorkspaceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = uuid.uuid4()
        request.policy = Policy(request.user)
        if request.user.is_authenticated:
            signed_in = request.session.get('signed_in_at')
            activity = request.session.get('last_activity_at')
            channel = request.session.get('auth_channel')
            now = int(timezone.now().timestamp())
            if channel == 'demo':
                eligible = request.user.is_demo and demo_available()
            else:
                eligible = (
                    channel == 'local' and settings.LOCAL_AUTH_ENABLED and not request.user.is_demo
                )
                if request.policy.requires_mfa and not request.session.get('verified_mfa'):
                    eligible = False
            if (
                not eligible
                or not signed_in
                or not activity
                or now - signed_in >= settings.SESSION_COOKIE_AGE
                or now - activity >= settings.SESSION_IDLE_SECONDS
            ):
                end_session(request, expired=True)
                request.policy = Policy(request.user)
            elif now - activity >= 30:
                request.session['last_activity_at'] = now
        if request.user.is_authenticated and settings.LANGUAGE_COOKIE_NAME not in request.COOKIES:
            translation.activate(request.user.language)
            request.LANGUAGE_CODE = request.user.language
        response = self.get_response(request)
        response.headers['Cache-Control'] = 'no-store, private'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'"
            + ('' if settings.PREVIEW else "; frame-ancestors 'none'")
        )
        return response
