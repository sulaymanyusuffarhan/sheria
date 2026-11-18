"""Environment-based application settings. Introduced: 2026-11-18; revisions: Git."""

import os
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_DIR = BASE_DIR / '.local'


def flag(name):
    return os.environ.get(name, '0') == '1'


ENV = os.environ.get('LSMS_ENV', 'development')
if ENV not in ('development', 'test', 'staging', 'production'):
    raise ImproperlyConfigured('Unknown LSMS_ENV.')
DEMO_MODE = flag('LSMS_DEMO')
PREVIEW = flag('LSMS_PREVIEW')
if (DEMO_MODE or PREVIEW) and ENV != 'development':
    raise ImproperlyConfigured('Demo and preview modes require LSMS_ENV=development.')
if PREVIEW and not DEMO_MODE:
    raise ImproperlyConfigured('Preview is restricted to a disposable demonstration database.')
DEBUG = ENV == 'development' and not PREVIEW

SECRET_KEY = os.environ.get('LSMS_SECRET_KEY') or (
    secrets.token_urlsafe(64) if ENV in ('development', 'test') else ''
)
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured('Set a strong LSMS_SECRET_KEY (at least 50 characters).')
ALLOWED_HOSTS = [
    host.strip() for host in os.environ.get('LSMS_ALLOWED_HOSTS', '').split(',') if host.strip()
]
if ENV in ('development', 'test'):
    ALLOWED_HOSTS += ['localhost', '127.0.0.1', '[::1]']
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get('LSMS_CSRF_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
]
if PREVIEW:
    # Only the explicitly enabled disposable preview accepts Arena proxy hosts.
    sandbox = os.environ.get('E2B_SANDBOX_ID')
    host = f'{os.environ.get("LSMS_PREVIEW_PORT", "8000")}-{sandbox}.e2b.app' if sandbox else ''
    if host:
        ALLOWED_HOSTS.append(host)
        CSRF_TRUSTED_ORIGINS.append(f'https://{host}')
if ENV in ('staging', 'production') and (not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS):
    raise ImproperlyConfigured('Use an explicit LSMS_ALLOWED_HOSTS allowlist.')

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'identity',
    'matters',
    'workspace',
    'casework',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'casework.storage.UploadLimitMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'lsms.middleware.WorkspaceMiddleware',
]
if not PREVIEW:
    MIDDLEWARE.append('django.middleware.clickjacking.XFrameOptionsMiddleware')

ROOT_URLCONF = 'lsms.urls'
WSGI_APPLICATION = 'lsms.wsgi.application'
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'workspace.context.workspace',
            ]
        },
    }
]

ENGINE = os.environ.get('LSMS_DB_ENGINE', 'sqlite')
if PREVIEW and ENGINE != 'sqlite':
    raise ImproperlyConfigured('Preview requires a disposable SQLite database.')
if ENGINE == 'postgresql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('LSMS_DB_NAME', 'lsms'),
            'USER': os.environ.get('LSMS_DB_USER', ''),
            'PASSWORD': os.environ.get('LSMS_DB_PASSWORD', ''),
            'HOST': os.environ.get('LSMS_DB_HOST', ''),
            'PORT': os.environ.get('LSMS_DB_PORT', ''),
            'OPTIONS': {'sslmode': os.environ.get('LSMS_DB_SSLMODE', 'prefer')},
        }
    }
elif ENGINE == 'sqlite' and ENV in ('development', 'test'):
    if not os.environ.get('LSMS_DB_PATH'):
        LOCAL_DIR.mkdir(mode=0o700, exist_ok=True)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.environ.get(
                'LSMS_DB_PATH', str(LOCAL_DIR / ('demo.sqlite3' if DEMO_MODE else 'lsms.sqlite3'))
            ),
            'OPTIONS': {'timeout': 20},
        }
    }
else:
    raise ImproperlyConfigured('Use PostgreSQL outside development/testing.')

AUTH_USER_MODEL = 'identity.User'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 12},
    },
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
]
MFA_KEY = os.environ.get('LSMS_MFA_KEY', '')
# Phase 1 has no approved institutional IdP. Block local sign-in in production.
LOCAL_AUTH_ENABLED = not DEMO_MODE and ENV in ('development', 'test', 'staging')
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_IDLE_SECONDS = 20 * 60
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = ENV in ('staging', 'production') or PREVIEW
SESSION_COOKIE_HTTPONLY = True
# The sandbox preview can be framed by the Arena UI; normal deployment cannot.
SESSION_COOKIE_SAMESITE = CSRF_COOKIE_SAMESITE = 'None' if PREVIEW else 'Lax'
CSRF_COOKIE_HTTPONLY = True
SECURE_SSL_REDIRECT = ENV in ('staging', 'production')
SECURE_HSTS_SECONDS = 31536000 if ENV == 'production' else 0
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
if PREVIEW or flag('LSMS_TRUST_TLS_PROXY'):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 180
DATA_UPLOAD_MAX_NUMBER_FILES = 4
DOCUMENT_KEY = os.environ.get('LSMS_DOCUMENT_KEY', '')
CLAMD_SOCKET = os.environ.get('LSMS_CLAMD_SOCKET', '')
DOCUMENT_FILE_LIMIT = 5 * 1024 * 1024
DOCUMENT_REQUEST_LIMIT = 21 * 1024 * 1024
# Do not stage private files in plaintext temporary files; cap the multipart body first.
FILE_UPLOAD_MAX_MEMORY_SIZE = DOCUMENT_REQUEST_LIMIT
FILE_UPLOAD_HANDLERS = ['django.core.files.uploadhandler.MemoryFileUploadHandler']
SILENCED_SYSTEM_CHECKS = []

TIME_ZONE = os.environ.get('LSMS_TIME_ZONE', 'Africa/Dar_es_Salaam')
try:
    ZoneInfo(TIME_ZONE)
except (KeyError, ValueError) as exc:
    raise ImproperlyConfigured('LSMS_TIME_ZONE must be an IANA time zone.') from exc
USE_TZ = True
LANGUAGE_CODE = 'en'
LANGUAGES = [('en', 'English'), ('sw', 'Kiswahili')]
LOCALE_PATHS = [BASE_DIR / 'locale']
USE_I18N = True
LANGUAGE_COOKIE_SECURE = SESSION_COOKIE_SECURE
LANGUAGE_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
}
CSRF_FAILURE_VIEW = 'workspace.views.csrf_failure'
