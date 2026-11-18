"""Authentication primitives and structured audit. Introduced: 2026-11-18; revisions: Git."""

import secrets
import uuid
from ipaddress import ip_address

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth import login, logout
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import AuditLog, AuthAttempt, TotpDevice, User
from .policy import Policy


@transaction.atomic
def limit_attempt(request, username='', *, purpose='login'):
    """A short DB-backed bucket per source and claimed account, without logging either."""
    source = request.META.get('REMOTE_ADDR', 'unknown')
    signals = [(f'{purpose}:source:{source}', 80 if purpose == 'demo' else 24)]
    if purpose != 'demo':
        signals.append((f'{purpose}:account:{username[:150]}', 7))
    window = int(timezone.now().timestamp() // 900)
    allowed = True
    for value, ceiling in sorted(signals):
        key = salted_hmac('lsms.signin.bucket', value, algorithm='sha256').hexdigest()
        bucket, _ = AuthAttempt.objects.get_or_create(key=key, window=window)
        if not AuthAttempt.objects.filter(pk=bucket.pk, count__lt=ceiling).update(count=F('count') + 1):
            allowed = False
    return allowed


def verify_totp(user, code):
    if not settings.MFA_KEY or len(code) != 6 or not code.isascii() or not code.isdecimal():
        return False
    device = TotpDevice.objects.filter(user=user).first()
    if device is None:
        return False
    try:
        secret = (
            Fernet(settings.MFA_KEY.encode('ascii'))
            .decrypt(bytes(device.encrypted_secret))
            .decode('ascii')
        )
    except (ValueError, InvalidToken):
        return False
    if not secret or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567' for ch in secret):
        return False
    totp = pyotp.TOTP(secret)
    now = int(timezone.now().timestamp() // totp.interval)
    for step in (now, now - 1, now + 1):
        if secrets.compare_digest(totp.at(step * totp.interval), code):
            return bool(
                TotpDevice.objects.filter(pk=device.pk, last_step__lt=step).update(last_step=step)
            )
    return False


def audit(request, action, *, area=AuditLog.Area.IDENTITY, target=None, detail=None, actor=None):
    actor = actor if actor is not None else getattr(request, 'user', None)
    if not actor or not actor.is_authenticated:
        actor = None
    policy = getattr(request, 'policy', None)
    if actor and (not policy or policy.user.pk != actor.pk):
        policy = Policy(actor)
    try:
        # Never trust an arbitrary client-supplied forwarding header.
        source = str(ip_address(request.META.get('REMOTE_ADDR', '')))
    except ValueError:
        source = None
    return AuditLog.objects.create(
        actor=actor,
        actor_role=policy.primary_role if actor and policy else '',
        is_demo=actor.is_demo if actor else settings.DEMO_MODE,
        area=area,
        action=action,
        target=str(target) if target is not None else '',
        detail=detail or {},
        correlation_id=getattr(request, 'request_id', uuid.uuid4()),
        session_correlation=request.session.get('audit_session'),
        source_address=source,
    )


@transaction.atomic
def start_session(request, user, *, verified_mfa=False, demo=False):
    # Flush even when switching between two sessions for the same identity.
    request.session.flush()
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    now = int(timezone.now().timestamp())
    request.session['signed_in_at'] = now
    request.session['last_activity_at'] = now
    request.session['auth_channel'] = 'demo' if demo else 'local'
    request.session['verified_mfa'] = verified_mfa
    request.session['audit_session'] = str(uuid.uuid4())
    audit(request, 'demo_signed_in' if demo else 'signed_in', actor=user, detail={'mfa': verified_mfa})


def end_session(request, *, expired=False):
    if request.user.is_authenticated:
        audit(request, 'session_expired' if expired else 'signed_out')
    logout(request)


def demo_available():
    from matters.models import LegalMatter

    from .models import Department, Directorate

    return (
        settings.ENV == 'development'
        and settings.DEMO_MODE
        and not User.objects.filter(is_demo=False).exists()
        and not LegalMatter.objects.filter(is_demo=False).exists()
        and not Department.objects.filter(is_demo=False).exists()
        and not Directorate.objects.filter(is_demo=False).exists()
    )
