"""
Production. AWS ap-south-1 (Mumbai).

🔴 Data residency is a compliance position and a sales argument (Doc 03 §7).
Nothing in this file may point at a region outside India.
"""

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from config.logging_filters import scrub, scrub_mapping

from .base import *

DEBUG = False
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS")

AWS_S3_REGION_NAME = "ap-south-1"
AWS_STORAGE_BUCKET_NAME = env("S3_BUCKET")
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = None

STORAGES = {
    "default": {"BACKEND": "storages.backends.s3.S3Storage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# Transport security (Doc 12 §13)
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True

DATABASES["default"]["OPTIONS"] = {"sslmode": "require"}

WHATSAPP_BACKEND = "apps.communications.backends.MetaCloudBackend"
EMAIL_CAMPAIGN_BACKEND = "apps.communications.backends.SESBackend"


# ---------------------------------------------------------------------------
# Sentry
#
# 🔴 R12 / Doc 12 §12: Sentry is a processor. PII is scrubbed before the event
# leaves the process, not by a server-side setting we cannot audit.
# ---------------------------------------------------------------------------
def _scrub_event(event, hint):
    if request := event.get("request"):
        if isinstance(request.get("data"), dict):
            request["data"] = scrub_mapping(request["data"])
        if isinstance(request.get("query_string"), str):
            request["query_string"] = scrub(request["query_string"])
        request.pop("cookies", None)
        headers = request.get("headers") or {}
        for header in ("Authorization", "Cookie", "X-Hub-Signature-256"):
            headers.pop(header, None)

    for exception in (event.get("exception") or {}).get("values") or []:
        for frame in (exception.get("stacktrace") or {}).get("frames") or []:
            if isinstance(frame.get("vars"), dict):
                frame["vars"] = scrub_mapping(frame["vars"])

    return event


sentry_sdk.init(
    dsn=env("SENTRY_DSN", default=""),
    integrations=[DjangoIntegration()],
    environment="production",
    traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.05),
    send_default_pii=False,  # 🔴 never flip this to True
    before_send=_scrub_event,
)
