"""
Settings, read from the repo-root `.env`.

One file, one service. This used to be shared with the Django app on purpose:
during the migration two services ran against one database, and a second copy
of DATABASE_URL is how they end up pointed at different ones — the failure
that is hardest to spot, because both halves work perfectly and disagree only
about the data. Django is retired, so the sharing is no longer load-bearing,
but the file location is unchanged and existing `.env` files keep working.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Configuration and local storage live at the repository root, one level above
# this flat backend package.
REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- Core -------------------------------------------------------------
    debug: bool = False
    # 🔴 `API_SECRET_KEY` is the name. `DJANGO_SECRET_KEY` is still read, and
    # deliberately: renaming an env var is a silent outage waiting for the next
    # deploy — the service starts, mints tokens with the *default* key, and
    # every previously issued session fails to verify. That reads like an
    # expiring session, not like a misconfiguration. Both names are accepted so
    # the rename can land before every environment has been updated.
    #
    # Drop the alias once render.yaml, the CI env and AWS Secrets Manager all
    # set API_SECRET_KEY. `startup_problems()` refuses to boot on the
    # development default in any case, so an environment that sets neither
    # cannot reach production silently.
    secret_key: str = Field(
        default="insecure-dev-key-override-in-env",
        validation_alias=AliasChoices("API_SECRET_KEY", "DJANGO_SECRET_KEY"),
    )
    database_url: str = "postgres://agricrm:agricrm_dev_only@localhost:5433/agricrm"
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # -- Auth (Doc 12 §1) -------------------------------------------------
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    # 🔴 Development-only switches. Same names and same meaning as the Django
    # app's, so an operator does not have to learn a second vocabulary and a
    # single `.env` cannot leave the two services disagreeing about whether
    # authentication is on.
    dev_no_auth: bool = False
    dev_no_mfa: bool = False

    # -- Invoice extraction agent (INVOICE.md) ----------------------------
    #
    # Optional. With no key the upload endpoint refuses cleanly and the form is
    # typed by hand — the agent is a convenience, never the only way in.
    invoice_extraction_provider: str = "anthropic"
    anthropic_api_key: str = ""
    nvidia_api_key: str = ""
    nvidia_vision_model: str = "meta/llama-3.2-90b-vision-instruct"
    # 🔴 `openai/gpt-oss-20b` is what INVOICE.md I-4a actually measured on a
    # real TEPL invoice: every field exact, 5-10s. The previous default,
    # `meta/llama-3.1-70b-instruct`, never matched that measurement and was
    # retired by the provider on 2026-08-26 — after which every extraction
    # answered HTTP 410. A hosted model has an end-of-life date; this one is
    # verified live, and `_nvidia_call` now names the model when it is not.
    nvidia_text_model: str = "openai/gpt-oss-20b"
    nvidia_timeout_seconds: int = 120

    # -- Collectors -------------------------------------------------------
    collector_contact_email: str = "data@thetaanalytics.in"
    scrapfly_api_key: str = ""

    # -- Object storage (INVOICE.md §12.8) --------------------------------
    #
    # Local by default so a developer needs no cloud account, and so the test
    # suite writes into a temporary directory rather than a bucket.
    storage_backend: str = "local"
    storage_root: str = str(REPO_ROOT / "var" / "storage")
    storage_bucket: str = ""
    storage_prefix: str = "invoices"
    aws_region: str = "ap-south-1"

    # -- Invoice copilot (INVOICE.md §12.3) -------------------------------
    #
    # 🔴 `fake` is the default, and it is not a stub — it is a deterministic
    # rule-based proposer that the evaluation suite runs against. A default of
    # `anthropic` would mean the tests either cost money, need a key, or get
    # skipped, and a skipped safety test is worse than an absent one.
    copilot_provider: str = "fake"
    copilot_model: str = "claude-opus-5"
    copilot_prompt_version: str = "v1"
    #: How long a human has to confirm a proposal. Short on purpose: the point
    #: of the hash binding is that the world may have moved on.
    copilot_proposal_ttl_minutes: int = 30
    copilot_max_proposals_per_hour: int = 60

    # -- GSTIN lookup (INVOICE.md §12.4) ----------------------------------
    #
    # 🔴 `fake` until a GSP contract exists. A real provider is enabled by
    # naming it here *and* supplying a key; naming it alone raises at startup
    # rather than silently falling back, because a silent fallback is how a
    # deployment ends up displaying fixture data as a live verification.
    gstin_lookup_provider: str = "fake"
    gstin_lookup_api_key: str = ""
    gstin_lookup_base_url: str = ""
    gstin_cache_ttl_hours: int = 168  # 7 days
    #: Beyond this, a cached verification is stale and the UI says so.
    gstin_stale_after_days: int = 30
    gstin_lookups_per_minute: int = 20

    # -- Delivery (INVOICE.md §12.5) --------------------------------------
    email_provider: str = "fake"
    email_from: str = "billing@thetaanalytics.in"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    whatsapp_provider: str = "fake"
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    #: 🔴 Verifies the signature on an inbound webhook. Without it the
    #: endpoint accepts nothing — see `domain/webhooks.py`.
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""

    # -- Payments ---------------------------------------------------------
    payment_gateway_provider: str = "fake"
    payment_gateway_key: str = ""
    payment_gateway_secret: str = ""
    payment_webhook_secret: str = ""
    #: How old a signed webhook may be before it is treated as a replay.
    webhook_replay_window_seconds: int = 300
    #: The VPA a manual UPI request pays into. Empty disables manual UPI
    #: rather than generating a link that goes nowhere.
    upi_vpa: str = ""
    upi_payee_name: str = ""

    @property
    def sqlalchemy_url(self) -> str:
        """
        SQLAlchemy needs an explicit driver; `.env` carries a libpq URL.

        🔴 asyncpg, not psycopg, and the reason is Windows.

        psycopg refuses to run async on Windows' default ProactorEventLoop,
        and uvicorn installs that policy itself when it starts the loop — so
        setting a different one beforehand does not survive. The service comes
        up, `/healthz` answers 200, and every database call fails. asyncpg has
        no such restriction and runs on either loop.

        The Django service keeps psycopg. Two drivers is a real cost, but a
        smaller one than a service that only works if nobody develops on
        Windows, and the schema is owned by `sql/schema.sql` rather than by
        either ORM — so there is one definition for both to agree with.

        Query parameters are dropped here because asyncpg does not accept
        libpq's spelling (`sslmode=require`); TLS is configured in
        `connect_args` instead. See `api/db.py`.
        """
        url = self.database_url.split("?")[0]

        if url.startswith("postgresql+asyncpg://"):
            return url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if url.startswith("postgresql+psycopg://"):
            url = url.replace("postgresql+psycopg://", "postgresql://", 1)
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)

    @property
    def requires_tls(self) -> bool:
        """
        Whether the database demands TLS.

        Read off the URL the operator wrote rather than guessed from the host
        name: a managed provider that needs `sslmode=require` says so, and a
        local container does not.
        """
        return "sslmode=require" in self.database_url or "ssl=true" in self.database_url

    @property
    def require_mfa(self) -> bool:
        """
        🔴 Doc 12 §1. MFA is mandatory for data_ops, campaign_manager,
        compliance and admin.

        Off only when `DEV_NO_MFA=1` *and* debug is on — the same pair of
        conditions the Django service requires, so one `.env` cannot leave the
        two disagreeing about whether a second factor is enforced. The `debug`
        half is what stops the flag reaching a deployed instance: it is the
        difference between "a developer skipped the TOTP step" and "privileged
        accounts on this server need only a password".
        """
        return not (self.dev_no_mfa and self.debug)

    @property
    def auth_enabled(self) -> bool:
        """🔴 Same shape as `require_mfa`, for the wider bypass."""
        return not (self.dev_no_auth and self.debug)

    @property
    def cors_origins(self) -> list[str]:
        """Exact browser origins, from a comma-separated env value."""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
