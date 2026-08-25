"""
Render-hosted staging.

🔴 THIS IS A STAGING ENVIRONMENT. IT MUST NOT HOLD PRODUCTION FARMER PII.

Two reasons, both from the specification rather than preference:

  R11 (Doc 05 §5)  — staging and dev never contain production PII.
  Doc 03 §7        — all personal data is stored and processed in ap-south-1
                     (Mumbai). Render has no India region; the closest is
                     Singapore. Real farmer data here would breach the
                     residency position that Doc 00 sells to cooperative
                     banks and government-linked buyers.

Use `manage.py generate_synthetic_data`. Production belongs on the AWS
ap-south-1 topology in config/settings/production.py.

Two Render free-tier constraints worth knowing before you rely on this:

  * Free web services sleep after 15 minutes idle and take ~50s to wake.
  * Free tier has no background workers, so Celery runs eagerly — inside the
    request. That is survivable for a demo and NOT survivable for Phase 4:
    a campaign send must not block an HTTP request, and the dispatch-time
    consent re-check needs a real worker. Budget a paid worker before then.
"""

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from config.logging_filters import scrub, scrub_mapping

from .base import *

DEBUG = False

# Render injects RENDER_EXTERNAL_HOSTNAME. Keep localhost for `render exec`.
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
if _host := env("RENDER_EXTERNAL_HOSTNAME", default=""):
    ALLOWED_HOSTS.append(_host)
ALLOWED_HOSTS += env.list("EXTRA_ALLOWED_HOSTS", default=[])

# ---------------------------------------------------------------------------
# Database — external managed Postgres (Neon / Supabase, ap-south-1)
#
# Not Render's own Postgres: the free tier there is deleted after 90 days and
# has no India region. See DEPLOYMENT.md.
# ---------------------------------------------------------------------------
DATABASES["default"]["OPTIONS"] = {"sslmode": "require"}
DATABASES["default"]["CONN_MAX_AGE"] = 0  # serverless Postgres pools upstream

# ---------------------------------------------------------------------------
# CORS
#
# The Vercel rewrite in frontend/vercel.json makes the browser see one origin,
# so CORS is normally not exercised. It is configured anyway so that pointing
# the frontend straight at this API (preview builds, curl, a second client)
# works without a scramble.
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOWED_ORIGIN_REGEXES = [r"^https://.*\.vercel\.app$"]
CORS_ALLOW_CREDENTIALS = False  # JWT travels in the Authorization header

CSRF_TRUSTED_ORIGINS = [
    f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")
]

# ---------------------------------------------------------------------------
# Transport security. Render terminates TLS at its proxy.
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# 🔴 Messaging stays mocked here.
#
# Doc 04 §8: staging uses a Meta test number and internal recipients only.
# A staging environment that can reach real farmers is one misconfiguration
# away from an unconsented send, which is the failure mode Doc 05 is written
# to prevent. Wiring live credentials here needs a deliberate decision and a
# recipient allow-list, not an environment variable.
# ---------------------------------------------------------------------------
WHATSAPP_BACKEND = "apps.communications.backends.MockWhatsAppBackend"
EMAIL_CAMPAIGN_BACKEND = "apps.communications.backends.MockEmailBackend"
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# No worker dyno on the free tier. Loud, because silently running imports and
# sends inside a web request is a performance and correctness trap.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)
CELERY_TASK_EAGER_PROPAGATES = True


def _scrub_event(event, hint):
    """R12 — scrub before the event leaves the process."""
    if request := event.get("request"):
        if isinstance(request.get("data"), dict):
            request["data"] = scrub_mapping(request["data"])
        if isinstance(request.get("query_string"), str):
            request["query_string"] = scrub(request["query_string"])
        request.pop("cookies", None)
        for header in ("Authorization", "Cookie", "X-Hub-Signature-256"):
            (request.get("headers") or {}).pop(header, None)
    return event


if _dsn := env("SENTRY_DSN", default=""):
    sentry_sdk.init(
        dsn=_dsn,
        integrations=[DjangoIntegration()],
        environment="staging-render",
        traces_sample_rate=0.2,
        send_default_pii=False,  # 🔴 never True
        before_send=_scrub_event,
    )
