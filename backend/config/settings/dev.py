"""Local development. Docker Compose provides Postgres and Redis."""

from .base import *

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

INSTALLED_APPS += ["silk"]
MIDDLEWARE.insert(0, "silk.middleware.SilkyMiddleware")

CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]  # Vite dev server

# 🔴 R11: dev never holds production PII. Use
# `manage.py generate_synthetic_data` — the command exists so that copying a
# production dump is never the path of least resistance.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Messaging providers are mocked in dev (Doc 04 §8). A misconfigured dev
# environment must not be able to reach Meta or SES.
WHATSAPP_BACKEND = "apps.communications.backends.MockWhatsAppBackend"
EMAIL_CAMPAIGN_BACKEND = "apps.communications.backends.MockEmailBackend"

# Run Celery tasks inline so a dev machine needs no worker process.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

AXES_ENABLED = False  # lockouts make local iteration miserable
