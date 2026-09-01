-- =====================================================================
--  AgriCRM — advanced invoice module
--  Theta Analytics · 2026-08-29
--
--  INVOICE.md §12.5 and phases I-7 to I-10: the AI proposal boundary,
--  GSTIN verification evidence, the delivery outbox, payment requests and
--  webhook reconciliation, reminders, and effective-dated tax knowledge.
--
--  Run order:
--    psql -f schema.sql
--    psql -f schema_invoice_advanced.sql
--    psql -f seed_reference.sql
--
--  🔴 This file is idempotent, and schema.sql is not.
--
--  schema.sql uses bare CREATE TYPE and is applied by dropping the schemas
--  first (scripts/db-reset.sh). That is correct for a file that defines the
--  whole world, and useless for adding tables to a database that already
--  holds invoices. So this one is written to be run repeatedly against a
--  live database: every statement is IF NOT EXISTS or guarded, and running
--  it twice changes nothing.
--
--  It is a separate file rather than an appended section for exactly that
--  reason — the two have different rules, and mixing them is how someone
--  eventually runs a destructive apply against a database with real rows in
--  it.
-- =====================================================================

SET search_path = crm, core, ref, public;

-- ---------------------------------------------------------------------
-- 1. Enumerated types
--
-- CLAUDE.md: new enum values are appended, never renamed or removed. The
-- guarded CREATE below means a value added by hand to a running database is
-- not silently reverted by a re-run — this file will not touch a type that
-- already exists, so a divergence is visible in a diff rather than papered
-- over here.
-- ---------------------------------------------------------------------
DO $$ BEGIN
  CREATE TYPE crm.ai_proposal_status AS ENUM (
    'pending',     -- generated, shown to a human, nothing written
    'confirmed',   -- a named human accepted this exact proposal hash
    'applied',     -- the diff reached a draft
    'rejected',    -- a human declined it
    'expired',     -- it aged out before confirmation
    'failed'       -- application raised; nothing partial was written
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  -- 🔴 The allow-list *is* the trust boundary (INVOICE.md §12.2). There is
  -- deliberately no 'issue', 'cancel', 'record_payment' or 'send' member:
  -- an action the copilot cannot name is an action it cannot request, and
  -- adding one here is a schema change a person has to make on purpose.
  CREATE TYPE crm.ai_proposal_action AS ENUM (
    'create_draft',
    'update_draft',
    'suggest_organisation_update',
    'explain_total'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE crm.check_severity AS ENUM ('info', 'warning', 'error');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE crm.delivery_status AS ENUM (
    'queued',     -- in the outbox, not yet claimed
    'claimed',    -- a worker holds it
    'sent',       -- the provider accepted it
    'delivered',  -- the provider reported delivery
    'failed',     -- permanent, or out of attempts
    'cancelled'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE crm.payment_request_status AS ENUM (
    'created',
    'awaiting_manual_confirmation',  -- a UPI link or QR. 🔴 Never a payment.
    'pending_provider',
    'succeeded',
    'failed',
    'expired',
    'cancelled'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE crm.webhook_processing_result AS ENUM (
    'pending',
    'processed',
    'duplicate',       -- the provider's event id was seen before
    'unmatched',       -- no invoice or amount match: goes to reconciliation
    'signature_failed',
    'replayed',        -- outside the freshness window
    'error'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  -- 🔴 'verification_unavailable' is a first-class outcome, not an error to
  -- be smoothed over. INVOICE.md §12.4: provider downtime must never be
  -- displayed as valid, and it cannot be if the vocabulary has a word for it.
  CREATE TYPE crm.gstin_verification_status AS ENUM (
    'valid_active',
    'valid_inactive',
    'cancelled',
    'provisional',
    'not_found',
    'invalid_format',
    'verification_unavailable',
    'error'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE crm.knowledge_review_status AS ENUM (
    'ai_suggested',   -- 🔴 never presentable as verified
    'under_review',
    'approved',
    'rejected',
    'superseded'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE crm.reminder_run_status AS ENUM (
    'preview',
    'confirmed',
    'sending',
    'completed',
    'cancelled',
    'expired'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------
-- 2. Stored objects
--
-- Source documents and rendered artifacts, addressed by hash. The bytes
-- live wherever the storage backend puts them; this table is the index,
-- and it is what makes "the PDF you hold is the PDF you sent" checkable
-- without opening a file.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm.stored_object (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  storage_key       text NOT NULL,
  backend           text NOT NULL DEFAULT 'local',   -- local / s3
  content_type      text NOT NULL,
  size_bytes        bigint NOT NULL CHECK (size_bytes >= 0),
  sha256            bytea NOT NULL,
  original_name     text,

  -- What this is for, so a retention sweep can act on a class of object
  -- rather than on a guess about the key's shape.
  purpose           text NOT NULL,        -- invoice_pdf / upload / webhook_payload
  retain_until      date,
  is_deleted        boolean NOT NULL DEFAULT false,

  billing_entity_id uuid REFERENCES crm.billing_entity(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        uuid,
  UNIQUE (backend, storage_key)
);
CREATE INDEX IF NOT EXISTS idx_stored_object_sha ON crm.stored_object(sha256);
CREATE INDEX IF NOT EXISTS idx_stored_object_purpose
  ON crm.stored_object(purpose, created_at DESC);

-- ---------------------------------------------------------------------
-- 3. AI proposals
--
-- 🔴 The whole point of this table is that an AI action is a *record* before
-- it is an effect. Nothing the copilot decides reaches an invoice without a
-- row here first, and that row carries who confirmed it, against exactly
-- which bytes, and what the draft looked like beforehand.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm.ai_proposal (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),

  -- The isolation boundary. Every retrieval and every apply is filtered on
  -- this, and it is taken from the caller's session, never from the request
  -- body (CLAUDE_INVOICE_BUILD_PROMPT: never trust tenant ids from a client).
  billing_entity_id   uuid NOT NULL REFERENCES crm.billing_entity(id),

  actor_user_id       uuid NOT NULL,            -- accounts_user.public_id
  action              crm.ai_proposal_action NOT NULL,
  status              crm.ai_proposal_status NOT NULL DEFAULT 'pending',

  -- The draft this touches. NULL for create_draft until it is applied.
  invoice_id          uuid REFERENCES crm.invoice(id) ON DELETE SET NULL,
  organisation_id     uuid REFERENCES core.organisation(id) ON DELETE SET NULL,

  model               text,
  prompt_version      text,
  provider            text,

  -- 🔴 Confirmation binds to this. Computed over the proposed patch, the
  -- action and the before-snapshot, so a draft edited between proposal and
  -- confirmation invalidates the confirmation rather than applying a diff
  -- against state the human never saw.
  proposal_sha256     bytea NOT NULL,
  input_sha256        bytea,                    -- the request text/transcript
  evidence            jsonb NOT NULL DEFAULT '[]'::jsonb,
  before_snapshot     jsonb NOT NULL DEFAULT '{}'::jsonb,
  proposed_patch      jsonb NOT NULL DEFAULT '{}'::jsonb,
  warnings            jsonb NOT NULL DEFAULT '[]'::jsonb,
  missing_fields      text[] NOT NULL DEFAULT '{}',
  confidence          numeric(4,3)
                        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),

  expires_at          timestamptz NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  confirmed_at        timestamptz,
  confirmed_by        uuid,
  applied_at          timestamptz,
  rejected_at         timestamptz,
  rejection_reason    text,
  error               text,

  latency_ms          integer,
  provider_cost_usd   numeric(10,6),

  -- A confirmation that names nobody is not a confirmation.
  CHECK (status <> 'confirmed' OR confirmed_by IS NOT NULL),
  CHECK (status <> 'applied'   OR confirmed_by IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_ai_proposal_entity
  ON crm.ai_proposal(billing_entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_proposal_invoice
  ON crm.ai_proposal(invoice_id) WHERE invoice_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ai_proposal_status
  ON crm.ai_proposal(status, expires_at);

-- 🔴 A proposal is append-mostly: the patch, the evidence and the hash it was
-- confirmed against are the audit record, and rewriting them after the fact
-- would leave a confirmation pointing at bytes nobody agreed to.
CREATE OR REPLACE FUNCTION crm.fn_ai_proposal_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.proposal_sha256 IS DISTINCT FROM OLD.proposal_sha256
     OR NEW.proposed_patch::text IS DISTINCT FROM OLD.proposed_patch::text
     OR NEW.action IS DISTINCT FROM OLD.action
     OR NEW.billing_entity_id IS DISTINCT FROM OLD.billing_entity_id THEN
    RAISE EXCEPTION
      'An AI proposal is immutable once created (id %). Create a new one.', OLD.id;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_ai_proposal_immutable ON crm.ai_proposal;
CREATE TRIGGER trg_ai_proposal_immutable
  BEFORE UPDATE ON crm.ai_proposal
  FOR EACH ROW EXECUTE FUNCTION crm.fn_ai_proposal_immutable();

-- ---------------------------------------------------------------------
-- 4. AI evaluation
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm.ai_evaluation_case (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  slug              text NOT NULL UNIQUE,
  title             text NOT NULL,
  kind              text NOT NULL,        -- extraction / proposal / safety
  -- 🔴 Redacted fixture text, never a customer's real document. The GSTINs
  -- and names in the fixtures are the two billing entities' own plus
  -- deliberately constructed ones.
  input_fixture     jsonb NOT NULL,
  expected          jsonb NOT NULL,
  regression_tags   text[] NOT NULL DEFAULT '{}',
  is_critical       boolean NOT NULL DEFAULT false,
  review_status     crm.knowledge_review_status NOT NULL DEFAULT 'approved',
  provenance        text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        uuid
);
CREATE INDEX IF NOT EXISTS idx_ai_eval_case_kind ON crm.ai_evaluation_case(kind);

CREATE TABLE IF NOT EXISTS crm.ai_evaluation_run (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  model             text NOT NULL,
  prompt_version    text NOT NULL,
  provider          text NOT NULL,
  started_at        timestamptz NOT NULL DEFAULT now(),
  finished_at       timestamptz,
  cases_total       integer NOT NULL DEFAULT 0,
  cases_passed      integer NOT NULL DEFAULT 0,
  critical_total    integer NOT NULL DEFAULT 0,
  critical_passed   integer NOT NULL DEFAULT 0,
  unsafe_requests   integer NOT NULL DEFAULT 0,
  notes             text
);

CREATE TABLE IF NOT EXISTS crm.ai_evaluation_result (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  run_id            uuid NOT NULL REFERENCES crm.ai_evaluation_run(id) ON DELETE CASCADE,
  case_id           uuid NOT NULL REFERENCES crm.ai_evaluation_case(id) ON DELETE CASCADE,
  passed            boolean NOT NULL,
  abstained         boolean NOT NULL DEFAULT false,
  -- Per field, so a bad GSTIN cannot hide inside an average (INVOICE.md §12.7).
  field_results     jsonb NOT NULL DEFAULT '{}'::jsonb,
  warnings          jsonb NOT NULL DEFAULT '[]'::jsonb,
  latency_ms        integer,
  cost_usd          numeric(10,6),
  detail            text,
  UNIQUE (run_id, case_id)
);

-- ---------------------------------------------------------------------
-- 5. GSTIN verification
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm.gstin_verification (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  billing_entity_id   uuid NOT NULL REFERENCES crm.billing_entity(id),
  gstin               text NOT NULL,
  provider            text NOT NULL,             -- fake / <gsp name>
  provider_reference  text,
  status              crm.gstin_verification_status NOT NULL,

  legal_name          text,
  trade_name          text,
  registration_type   text,
  taxpayer_status     text,
  effective_from      date,
  cancellation_date   date,
  principal_address   text,
  state_code          char(2),

  -- 🔴 The hash, not the body. The provider's reply describes a real
  -- business; keeping it whole in a hot table spreads identity data across
  -- every backup, and the audit question is only ever "is this the reply we
  -- acted on".
  raw_response_sha256 bytea,
  raw_object_id       uuid REFERENCES crm.stored_object(id),

  checked_at          timestamptz NOT NULL DEFAULT now(),
  expires_at          timestamptz,
  error_code          text,
  error_detail        text,
  requested_by        uuid,

  CHECK (gstin = upper(gstin))
);
CREATE INDEX IF NOT EXISTS idx_gstin_verif_lookup
  ON crm.gstin_verification(billing_entity_id, gstin, provider, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_gstin_verif_expiry
  ON crm.gstin_verification(expires_at) WHERE expires_at IS NOT NULL;

-- Issue-time evidence. 🔴 Immutable: re-verifying a GSTIN next year creates a
-- new gstin_verification row and must not rewrite what an issued invoice was
-- checked against (INVOICE.md §12.5).
CREATE TABLE IF NOT EXISTS crm.invoice_gstin_check (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id          uuid NOT NULL REFERENCES crm.invoice(id) ON DELETE CASCADE,
  verification_id     uuid REFERENCES crm.gstin_verification(id),
  checked_gstin       text NOT NULL,
  local_result        text NOT NULL,             -- valid / invalid_format / govt_uin
  live_status         crm.gstin_verification_status,
  blocking_reasons    text[] NOT NULL DEFAULT '{}',
  mismatches          jsonb NOT NULL DEFAULT '[]'::jsonb,

  override_by         uuid,
  override_reason     text,
  override_at         timestamptz,

  created_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,

  -- An override with no reason is an override nobody can review afterwards.
  CHECK (override_by IS NULL OR (override_reason IS NOT NULL AND override_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_invoice_gstin_check_invoice
  ON crm.invoice_gstin_check(invoice_id, created_at DESC);

CREATE OR REPLACE FUNCTION crm.fn_gstin_check_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.invoice_id       IS DISTINCT FROM OLD.invoice_id
     OR NEW.checked_gstin IS DISTINCT FROM OLD.checked_gstin
     OR NEW.verification_id IS DISTINCT FROM OLD.verification_id
     OR NEW.live_status   IS DISTINCT FROM OLD.live_status THEN
    RAISE EXCEPTION
      'Issue-time GSTIN evidence is immutable (invoice %). Record a new check.',
      OLD.invoice_id;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_gstin_check_immutable ON crm.invoice_gstin_check;
CREATE TRIGGER trg_gstin_check_immutable
  BEFORE UPDATE ON crm.invoice_gstin_check
  FOR EACH ROW EXECUTE FUNCTION crm.fn_gstin_check_immutable();

-- ---------------------------------------------------------------------
-- 6. Pre-issue checks
--
-- The result of a check run, kept so that "what did we know when we issued
-- this" has an answer, and so an acknowledged warning names the person who
-- acknowledged it.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm.invoice_check_run (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id        uuid NOT NULL REFERENCES crm.invoice(id) ON DELETE CASCADE,
  ran_at            timestamptz NOT NULL DEFAULT now(),
  ran_by            uuid,
  -- 🔴 The hash of the invoice state the checks ran against. Issue re-runs
  -- the checks and compares; a draft edited after a clean run is checked
  -- again rather than issued on a stale pass.
  invoice_sha256    bytea NOT NULL,
  blocking_count    integer NOT NULL DEFAULT 0,
  warning_count     integer NOT NULL DEFAULT 0,
  results           jsonb NOT NULL DEFAULT '[]'::jsonb,
  is_issue_evidence boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_invoice_check_run_invoice
  ON crm.invoice_check_run(invoice_id, ran_at DESC);

CREATE TABLE IF NOT EXISTS crm.invoice_check_ack (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id        uuid NOT NULL REFERENCES crm.invoice(id) ON DELETE CASCADE,
  check_code        text NOT NULL,
  severity          crm.check_severity NOT NULL,
  reason            text NOT NULL,
  acknowledged_by   uuid NOT NULL,
  acknowledged_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (invoice_id, check_code, acknowledged_by)
);

-- ---------------------------------------------------------------------
-- 7. Delivery outbox
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm.invoice_delivery (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id          uuid NOT NULL REFERENCES crm.invoice(id) ON DELETE CASCADE,
  billing_entity_id   uuid NOT NULL REFERENCES crm.billing_entity(id),
  channel             comm.channel NOT NULL,
  recipient           text NOT NULL,             -- the exact address dialled
  recipient_name      text,

  subject             text,
  -- The message as it was approved, not a template id to re-render later.
  -- Re-rendering is how a delivery record stops describing what was sent.
  body_snapshot       text NOT NULL,
  template_version    text,

  -- 🔴 The artifact. A resend is a new row; the original keeps its hash.
  pdf_sha256          bytea,
  pdf_object_id       uuid REFERENCES crm.stored_object(id),

  -- Confirmation binds to a frozen preview, the same way a proposal does.
  preview_sha256      bytea NOT NULL,
  confirmed_by        uuid NOT NULL,
  confirmed_at        timestamptz NOT NULL DEFAULT now(),

  status              crm.delivery_status NOT NULL DEFAULT 'queued',
  attempts            integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts        integer NOT NULL DEFAULT 5,
  next_attempt_at     timestamptz NOT NULL DEFAULT now(),
  claimed_at          timestamptz,
  claimed_by          text,                      -- worker identity
  provider            text,
  provider_message_id text,
  sent_at             timestamptz,
  delivered_at        timestamptz,
  failed_at           timestamptz,
  error_code          text,
  error_detail        text,

  -- 🔴 What makes a retry safe. A caller replaying "send this" gets the same
  -- row rather than a second email to a customer.
  idempotency_key     text NOT NULL,
  reminder_id         uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_delivery_invoice
  ON crm.invoice_delivery(invoice_id, created_at DESC);
-- The outbox claim query: oldest due work first, ignoring anything terminal.
CREATE INDEX IF NOT EXISTS idx_delivery_due
  ON crm.invoice_delivery(next_attempt_at)
  WHERE status IN ('queued', 'claimed');

-- ---------------------------------------------------------------------
-- 8. Reminders
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm.reminder_policy (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  billing_entity_id   uuid NOT NULL REFERENCES crm.billing_entity(id),
  name                text NOT NULL,
  channel             comm.channel NOT NULL,
  -- Days overdue at which this policy has something to say.
  trigger_days        integer[] NOT NULL DEFAULT '{7,15,30}',
  template_body       text NOT NULL,
  template_version    text NOT NULL DEFAULT 'v1',

  quiet_hour_start    smallint NOT NULL DEFAULT 20 CHECK (quiet_hour_start BETWEEN 0 AND 23),
  quiet_hour_end      smallint NOT NULL DEFAULT 9  CHECK (quiet_hour_end BETWEEN 0 AND 23),
  timezone            text NOT NULL DEFAULT 'Asia/Kolkata',
  min_days_between    integer NOT NULL DEFAULT 7 CHECK (min_days_between >= 0),
  max_per_invoice     integer NOT NULL DEFAULT 4 CHECK (max_per_invoice >= 0),

  -- 🔴 Off unless a named person turned it on, with a scope and a ceiling.
  -- A scheduled job may always *prepare*; this is what lets it send.
  autosend_enabled    boolean NOT NULL DEFAULT false,
  autosend_max_per_run integer NOT NULL DEFAULT 0 CHECK (autosend_max_per_run >= 0),
  autosend_enabled_by uuid,
  autosend_enabled_at timestamptz,
  is_active           boolean NOT NULL DEFAULT true,
  created_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  UNIQUE (billing_entity_id, name),
  CHECK (NOT autosend_enabled
         OR (autosend_enabled_by IS NOT NULL AND autosend_max_per_run > 0))
);

CREATE TABLE IF NOT EXISTS crm.reminder_run (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  billing_entity_id   uuid NOT NULL REFERENCES crm.billing_entity(id),
  policy_id           uuid REFERENCES crm.reminder_policy(id),
  status              crm.reminder_run_status NOT NULL DEFAULT 'preview',
  -- The frozen preview. Confirmation quotes this hash back; a candidate list
  -- that changed between preview and confirm is refused rather than sent.
  preview_sha256      bytea NOT NULL,
  candidates          jsonb NOT NULL DEFAULT '[]'::jsonb,
  skipped             jsonb NOT NULL DEFAULT '[]'::jsonb,
  is_autosend         boolean NOT NULL DEFAULT false,
  created_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  expires_at          timestamptz NOT NULL,
  confirmed_at        timestamptz,
  confirmed_by        uuid,
  completed_at        timestamptz,
  CHECK (status NOT IN ('confirmed', 'sending', 'completed') OR confirmed_by IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS crm.invoice_reminder (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id          uuid NOT NULL REFERENCES crm.invoice(id) ON DELETE CASCADE,
  policy_id           uuid REFERENCES crm.reminder_policy(id),
  run_id              uuid REFERENCES crm.reminder_run(id) ON DELETE SET NULL,
  channel             comm.channel NOT NULL,
  recipient           text NOT NULL,
  message_snapshot    text NOT NULL,
  days_overdue        integer NOT NULL,
  scheduled_for       timestamptz NOT NULL,
  sent_at             timestamptz,
  approved_by         uuid NOT NULL,
  delivery_id         uuid REFERENCES crm.invoice_delivery(id),
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_invoice_reminder_invoice
  ON crm.invoice_reminder(invoice_id, created_at DESC);
-- 🔴 Frequency capping needs this to be cheap; it is checked per candidate on
-- every preview.
CREATE INDEX IF NOT EXISTS idx_invoice_reminder_recent
  ON crm.invoice_reminder(recipient, created_at DESC);

-- ---------------------------------------------------------------------
-- 9. Payment requests and webhooks
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm.payment_request (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id          uuid NOT NULL REFERENCES crm.invoice(id) ON DELETE CASCADE,
  billing_entity_id   uuid NOT NULL REFERENCES crm.billing_entity(id),
  provider            text NOT NULL,             -- manual_upi / fake_gateway / <gateway>
  provider_reference  text,
  amount              numeric(14,2) NOT NULL CHECK (amount > 0),
  currency            char(3) NOT NULL DEFAULT 'INR',
  -- The UPI URI or the gateway's hosted URL. Not a secret, but not a payment.
  payload_url         text,
  qr_svg              text,
  status              crm.payment_request_status NOT NULL DEFAULT 'created',
  expires_at          timestamptz,
  idempotency_key     text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  settled_at          timestamptz,
  payment_id          uuid REFERENCES crm.invoice_payment(id) ON DELETE SET NULL,
  error_detail        text,
  UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_payment_request_invoice
  ON crm.payment_request(invoice_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_request_provider_ref
  ON crm.payment_request(provider, provider_reference)
  WHERE provider_reference IS NOT NULL;

CREATE TABLE IF NOT EXISTS crm.payment_webhook_event (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  provider            text NOT NULL,
  -- 🔴 The uniqueness that makes redelivery harmless. A gateway retries on
  -- any non-2xx and on its own timeouts; without this a duplicate delivery
  -- is a duplicate payment row and a customer's invoice reads overpaid.
  provider_event_id   text NOT NULL,
  event_type          text,
  signature_verified  boolean NOT NULL DEFAULT false,
  received_at         timestamptz NOT NULL DEFAULT now(),
  provider_timestamp  timestamptz,
  raw_object_id       uuid REFERENCES crm.stored_object(id),
  raw_sha256          bytea NOT NULL,
  processing_result   crm.webhook_processing_result NOT NULL DEFAULT 'pending',
  processed_at        timestamptz,
  payment_request_id  uuid REFERENCES crm.payment_request(id) ON DELETE SET NULL,
  invoice_id          uuid REFERENCES crm.invoice(id) ON DELETE SET NULL,
  payment_id          uuid REFERENCES crm.invoice_payment(id) ON DELETE SET NULL,
  amount              numeric(14,2),
  currency            char(3),
  reference           text,
  mismatch_detail     text,
  UNIQUE (provider, provider_event_id)
);
CREATE INDEX IF NOT EXISTS idx_payment_webhook_unresolved
  ON crm.payment_webhook_event(processing_result, received_at DESC);

-- A customer said they would pay on a date. Deterministic input to the
-- collections ranking, and the only "promise" the system holds.
CREATE TABLE IF NOT EXISTS crm.payment_promise (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id          uuid NOT NULL REFERENCES crm.invoice(id) ON DELETE CASCADE,
  promised_on         date NOT NULL,
  promised_amount     numeric(14,2) CHECK (promised_amount IS NULL OR promised_amount > 0),
  note                text,
  contact_name        text,
  recorded_by         uuid NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  kept                boolean
);
CREATE INDEX IF NOT EXISTS idx_payment_promise_invoice
  ON crm.payment_promise(invoice_id, promised_on DESC);

-- ---------------------------------------------------------------------
-- 10. Inbound messaging identity
--
-- 🔴 A WhatsApp sender is bound to exactly one billing entity and one user.
-- An unknown sender resolves to nothing and therefore reads nothing — the
-- binding is the authorisation, so it lives in the database rather than in a
-- config file someone edits on a Friday.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm.messaging_identity (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  channel             comm.channel NOT NULL,
  sender_address      text NOT NULL,             -- E.164 for whatsapp
  billing_entity_id   uuid NOT NULL REFERENCES crm.billing_entity(id),
  user_id             uuid NOT NULL,             -- accounts_user.public_id
  is_active           boolean NOT NULL DEFAULT true,
  authorised_by       uuid NOT NULL,
  authorised_at       timestamptz NOT NULL DEFAULT now(),
  revoked_at          timestamptz,
  UNIQUE (channel, sender_address)
);

CREATE TABLE IF NOT EXISTS crm.inbound_invoice_message (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  channel             comm.channel NOT NULL,
  provider            text NOT NULL,
  provider_message_id text NOT NULL,
  sender_address      text NOT NULL,
  identity_id         uuid REFERENCES crm.messaging_identity(id),
  signature_verified  boolean NOT NULL DEFAULT false,
  received_at         timestamptz NOT NULL DEFAULT now(),
  kind                text NOT NULL DEFAULT 'text',   -- text / voice / document
  body                text,
  -- 🔴 Transcript and confidence, not the audio. Retaining a voice note needs
  -- a consent basis this module does not have (INVOICE.md §12.3 A).
  transcript          text,
  transcript_confidence numeric(4,3),
  proposal_id         uuid REFERENCES crm.ai_proposal(id) ON DELETE SET NULL,
  handled             boolean NOT NULL DEFAULT false,
  detail              text,
  UNIQUE (provider, provider_message_id)
);

-- ---------------------------------------------------------------------
-- 11. Effective-dated tax knowledge
--
-- 🔴 Retrieval is by the invoice's date, never by today's. A rate that
-- changed in July does not retroactively apply to a June document, and a
-- table without effective dates cannot express that at all.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm.tax_code_knowledge (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  code                text NOT NULL,             -- HSN or SAC
  code_kind           text NOT NULL DEFAULT 'sac',
  description         text NOT NULL,
  gst_rate_pct        numeric(5,2),
  jurisdiction        text NOT NULL DEFAULT 'IN',
  effective_from      date NOT NULL,
  effective_to        date,
  -- Where it came from. A suggestion with no citation is an opinion.
  source_title        text NOT NULL,
  source_url          text,
  source_object_id    uuid REFERENCES crm.stored_object(id),
  review_status       crm.knowledge_review_status NOT NULL DEFAULT 'ai_suggested',
  reviewed_by         uuid,
  reviewer_name       text,
  reviewed_at         timestamptz,
  keywords            text[] NOT NULL DEFAULT '{}',
  notes               text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  CHECK (effective_to IS NULL OR effective_to >= effective_from),
  CHECK (review_status <> 'approved' OR reviewed_by IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_tax_knowledge_code
  ON crm.tax_code_knowledge(code, effective_from DESC);
CREATE INDEX IF NOT EXISTS idx_tax_knowledge_effective
  ON crm.tax_code_knowledge(effective_from, effective_to);

-- ---------------------------------------------------------------------
-- 12. Contract / PO rates
--
-- What was agreed, so "the rate on this invoice is not the rate in the PO"
-- is a check against evidence rather than a feeling. Populated by hand or
-- from a PO document until crm.project lands its own contract records.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crm.contract_rate (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  billing_entity_id   uuid NOT NULL REFERENCES crm.billing_entity(id),
  organisation_id     uuid REFERENCES core.organisation(id) ON DELETE CASCADE,
  project_id          uuid REFERENCES crm.project(id) ON DELETE SET NULL,
  buyer_order_no      text,
  hsn_sac             text,
  description         text,
  unit                crm.billing_unit NOT NULL,
  rate                numeric(14,4) NOT NULL CHECK (rate >= 0),
  rate_is_tax_inclusive boolean NOT NULL DEFAULT false,
  tolerance_pct       numeric(5,2) NOT NULL DEFAULT 0,
  valid_from          date NOT NULL,
  valid_to            date,
  source_reference    text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX IF NOT EXISTS idx_contract_rate_org
  ON crm.contract_rate(organisation_id, valid_from DESC);

-- ---------------------------------------------------------------------
-- 13. Columns added to existing tables
--
-- ADD COLUMN IF NOT EXISTS, so this file stays runnable against a database
-- that already has them.
-- ---------------------------------------------------------------------
ALTER TABLE crm.invoice
  ADD COLUMN IF NOT EXISTS pdf_object_id uuid REFERENCES crm.stored_object(id);

ALTER TABLE crm.invoice_extraction
  ADD COLUMN IF NOT EXISTS billing_entity_id uuid REFERENCES crm.billing_entity(id),
  ADD COLUMN IF NOT EXISTS source_object_id uuid REFERENCES crm.stored_object(id),
  ADD COLUMN IF NOT EXISTS page_count integer,
  ADD COLUMN IF NOT EXISTS extraction_path text,      -- text_layer / vision
  ADD COLUMN IF NOT EXISTS evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS duplicate_of_invoice_id uuid REFERENCES crm.invoice(id),
  ADD COLUMN IF NOT EXISTS duplicate_reasons text[] NOT NULL DEFAULT '{}',
  -- What the human ended up with, beside what the model proposed.
  ADD COLUMN IF NOT EXISTS accepted_values jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS prompt_version text;

-- 🔴 The same file uploaded twice is the commonest way a document is billed
-- twice. Partial, because sha256 is null for a record that failed before the
-- bytes were hashed.
CREATE INDEX IF NOT EXISTS idx_invextract_sha
  ON crm.invoice_extraction(sha256) WHERE sha256 IS NOT NULL;

-- Contact details the delivery outbox addresses. On the organisation rather
-- than the invoice: an invoice snapshot is what was printed, and a billing
-- contact is current information that must not be frozen into a document.
ALTER TABLE core.organisation
  ADD COLUMN IF NOT EXISTS billing_email text,
  ADD COLUMN IF NOT EXISTS billing_phone text,
  ADD COLUMN IF NOT EXISTS billing_contact_name text,
  -- 🔴 R6/R7 in miniature: an opt-out is checked at dispatch, and it lives
  -- next to the address it suppresses.
  ADD COLUMN IF NOT EXISTS billing_opt_out boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS billing_opt_out_at timestamptz,
  -- gstin_policy: 'warn' | 'require_current' — whether a stale or missing
  -- live verification blocks issue for this customer.
  ADD COLUMN IF NOT EXISTS gstin_policy text NOT NULL DEFAULT 'warn';

-- =====================================================================
--  End of advanced invoice schema
-- =====================================================================
