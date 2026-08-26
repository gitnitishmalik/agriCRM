"""
Shared settings. Environment-specific modules import * from here and override.

Never put a secret in this file or any sibling. Secrets come from the
environment, which in staging/production is AWS Secrets Manager (Doc 12 §6).
"""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)

# Load .env from the repo root if present. Real environments inject these as
# process env vars (AWS Secrets Manager -> ECS task definition), where this is
# a no-op — read_env never overrides an already-set variable.
if (_dotenv := BASE_DIR.parent / ".env").exists():
    environ.Env.read_env(_dotenv)

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-override-in-env")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

AUTH_USER_MODEL = "accounts.User"
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Applications
#
# One Django app per bounded context (Doc 03 §9). There is deliberately no
# apps.core — it becomes a dumping ground and every app imports it circularly.
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
    "django_celery_beat",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "axes",
]

LOCAL_APPS = [
    "apps.accounts",  # users, roles, territories, MFA
    "apps.geography",  # ref.* — states, districts, blocks, villages, LGD sync
    "apps.organisations",  # organisation + type profiles, people, roles, contacts
    "apps.farmers",  # farmer, land, crops, livestock, org links
    "apps.dataquality",  # sources, provenance, scoring, dedupe, imports, merges
    "apps.communications",  # consent, templates, campaigns, whatsapp, email
    "apps.projects",  # project registry
    "apps.pipeline",  # leads, opportunities, stage history
    "apps.fieldops",  # agents, territories, visits, targets, mobile sync
    "apps.activities",  # activity feed, tasks, notifications
    "apps.reporting",  # dashboards, saved views, exports
    "apps.auditing",  # change log, access log, DSR handling
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must come last so it sees the authenticated user (django-axes requirement)
    "axes.middleware.AxesMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
#
# The schema is owned by sql/schema.sql, not by Django models. Django manages
# its own tables (auth, sessions, celery beat) in the public schema; the
# business schemas (ref/core/comm/crm/dq/audit) are applied by DDL and mapped
# with managed = False models. See CLAUDE.md.
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL", default="postgres://agricrm:agricrm_dev_only@localhost:5432/agricrm"
    ),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("CONN_MAX_AGE", default=60)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",  # must be first
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},  # Doc 12 §13
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Doc 12 §13: lockout after 10 failures in 15 minutes
AXES_FAILURE_LIMIT = 10
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]

# ---------------------------------------------------------------------------
# DRF + JWT (Doc 11 §1-2)
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Cursor pagination: offset breaks on large, actively-written tables (Doc 11 §1).
    # DRF's own class orders on `-created`, which no model here has.
    "DEFAULT_PAGINATION_CLASS": "config.pagination.TimelineCursorPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_CLASSES": ("rest_framework.throttling.ScopedRateThrottle",),
    "DEFAULT_THROTTLE_RATES": {
        "user": "1000/hour",
        "bulk": "60/hour",
        "export": "20/hour",
        "sync": "300/hour",
    },
    "EXCEPTION_HANDLER": "config.exceptions.agricrm_exception_handler",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "AgriCRM API",
    "DESCRIPTION": "Farmer, FPO & Sugar Mill CRM for Theta Analytics",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
}

# ---------------------------------------------------------------------------
# Celery
#
# Separate queues so a bulk import cannot starve message delivery (Doc 04 §2).
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", default="redis://localhost:6379/1")
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "apps.dataquality.tasks.*": {"queue": "import"},
    "apps.communications.tasks.send_*": {"queue": "messaging"},
    "collectors.*": {"queue": "heavy"},
}
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/2"),
    }
}

# ---------------------------------------------------------------------------
# i18n / tz
#
# Doc 01 §5: UI English + Hindi at v1; data entry accepts Devanagari.
# All timestamps stored UTC, displayed Asia/Kolkata.
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-in"
LANGUAGES = [("en", "English"), ("hi", "हिन्दी")]
TIME_ZONE = "UTC"
DISPLAY_TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Domain constants that code must not re-derive locally
# ---------------------------------------------------------------------------

# Doc 10 §2.6 — enforced in the send path, not just campaign config
QUIET_HOURS_START = 21  # 21:00 IST
QUIET_HOURS_END = 8  # 08:00 IST
MAX_MESSAGES_PER_WEEK_PER_RECIPIENT = 3

# Doc 10 §4.3 — auto-pause thresholds
CAMPAIGN_AUTOPAUSE_OPTOUT_RATE = 0.01
CAMPAIGN_AUTOPAUSE_FAILURE_RATE = 0.05

# Doc 06 §2.2 — entity resolution. Start auto-merge conservative at 0.96 and
# lower to 0.92 only once precision is measured against a labelled set.
DEDUPE_AUTO_MERGE_THRESHOLD = 0.96
DEDUPE_REVIEW_THRESHOLD = 0.75

# Doc 12 §8 — export alerting
EXPORT_REASON_REQUIRED_ABOVE = 1_000
EXPORT_ALERT_THRESHOLD = 10_000

# ---------------------------------------------------------------------------
# Logging
#
# R12: structured JSON, PII scrubbed, retained one year (DPDP Rule 6).
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "scrub_pii": {"()": "config.logging_filters.ScrubPIIFilter"},
    },
    "formatters": {
        "json": {
            "()": "config.logging_filters.JSONFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["scrub_pii"],
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
    },
}
