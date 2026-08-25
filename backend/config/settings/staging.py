"""
Staging — single-AZ, scaled down.

🔴 R11 / Doc 04 §8: staging holds synthetic or irreversibly anonymised data.
Never a production PII copy. This is the most common way personal data leaks
and it is entirely avoidable.

The guard below is deliberately loud. If a restore-from-production ever puts
real data here, the environment should refuse to boot rather than quietly
serve it.
"""

from .production import *

DEBUG = False

sentry_sdk.init(
    dsn=env("SENTRY_DSN", default=""),
    integrations=[DjangoIntegration()],
    environment="staging",
    traces_sample_rate=0.2,
    send_default_pii=False,
    before_send=_scrub_event,
)

# Meta test number, internal recipients only (Doc 04 §8).
WHATSAPP_ALLOWED_RECIPIENTS = env.list("WHATSAPP_ALLOWED_RECIPIENTS", default=[])
REQUIRE_SYNTHETIC_DATA_MARKER = True
