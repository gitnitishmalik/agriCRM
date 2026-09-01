-- =====================================================================
--  AgriCRM — PostgreSQL 16 schema
--  Theta Analytics · v1.0 · 2026-08-24
--
--  Run order:
--    psql -f schema.sql
--    psql -f seed_reference.sql
--
--  Requires: postgresql-16, postgis-3, pg_trgm, btree_gist, uuid-ossp
--  Note: PostGIS columns are created only if the extension is present.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. Extensions & schemas
-- ---------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- fuzzy name matching (dedupe)
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- exclusion constraints on ranges
CREATE EXTENSION IF NOT EXISTS unaccent;
-- CREATE EXTENSION IF NOT EXISTS postgis;   -- uncomment on a PostGIS-enabled instance

CREATE SCHEMA IF NOT EXISTS core;      -- master data
CREATE SCHEMA IF NOT EXISTS comm;      -- consent + messaging
CREATE SCHEMA IF NOT EXISTS crm;       -- projects, pipeline, agents
CREATE SCHEMA IF NOT EXISTS dq;        -- data quality / provenance
CREATE SCHEMA IF NOT EXISTS audit;     -- audit + compliance
CREATE SCHEMA IF NOT EXISTS ref;       -- reference / lookup data

SET search_path = core, ref, public;

-- ---------------------------------------------------------------------
-- 1. Enumerated types
-- ---------------------------------------------------------------------
CREATE TYPE core.org_type AS ENUM (
  'fpo',                 -- Farmer Producer Organisation (Companies Act / Co-op)
  'acs',                 -- Agriculture Cooperative Society / PACS
  'sugar_mill',
  'cooperative_federation',
  'input_dealer',
  'ngo_promoting_institution',
  'government_body',
  'private_company',
  'bank_nbfc',
  'other'
);

CREATE TYPE core.org_status AS ENUM (
  'prospect','active','dormant','defunct','merged','blacklisted'
);

CREATE TYPE core.legal_form AS ENUM (
  'producer_company','cooperative_society','section_8_company','private_limited',
  'public_limited','llp','partnership','proprietorship','trust','society',
  'statutory_body','unregistered','unknown'
);

CREATE TYPE core.mill_ownership AS ENUM ('private','cooperative','public_sector','joint_sector');

CREATE TYPE core.gender AS ENUM ('male','female','other','undisclosed');

CREATE TYPE core.social_category AS ENUM ('general','obc','sc','st','minority','undisclosed');

CREATE TYPE core.farmer_class AS ENUM ('marginal','small','semi_medium','medium','large','unknown');

CREATE TYPE core.land_tenure AS ENUM ('owned','leased_in','leased_out','sharecropped','community','encroached','unknown');

CREATE TYPE core.irrigation_source AS ENUM (
  'canal','tubewell','borewell','open_well','tank_pond','river_lift',
  'drip','sprinkler','rainfed','other','unknown'
);

CREATE TYPE core.season AS ENUM ('kharif','rabi','zaid','perennial','annual');

CREATE TYPE core.contact_kind AS ENUM ('mobile','landline','whatsapp','email','fax');

CREATE TYPE core.verification_state AS ENUM (
  'unverified','pending','verified','failed','invalid','do_not_contact'
);

CREATE TYPE core.role_type AS ENUM (
  'managing_director','chief_executive','chairman','vice_chairman','director',
  'secretary','treasurer','board_member','member_farmer','shareholder',
  'cane_manager','procurement_head','general_manager','unit_head',
  'accountant','field_officer','promoter','nodal_officer','other'
);

CREATE TYPE dq.quality_tier AS ENUM ('gold','silver','bronze','quarantine');

CREATE TYPE dq.source_kind AS ENUM (
  'public_registry',        -- MCA, SFAC, NCDC, LGD, state portals
  'open_government_data',   -- data.gov.in, AGMARKNET
  'official_website',       -- organisation's own published contact details
  'industry_directory',     -- ISMA / NFCSF / chamber directories
  'partner_agreement',      -- FPO/mill MoU data share
  'field_collection',       -- our agent, with consent captured
  'inbound_signup',         -- farmer self-registered
  'theta_analytics',        -- our own prior database
  'purchased_licensed',     -- licensed dataset with a contract
  'manual_entry',
  'inferred',               -- derived/computed, not observed
  'unknown'
);

CREATE TYPE comm.channel AS ENUM ('whatsapp','sms','email','voice','postal','in_app');

CREATE TYPE comm.consent_status AS ENUM ('never_asked','opted_in','opted_out','withdrawn','expired','bounced_out');

CREATE TYPE comm.consent_purpose AS ENUM (
  'transactional','service_update','advisory','marketing','survey','project_specific'
);

CREATE TYPE comm.message_state AS ENUM (
  'queued','sent','delivered','read','failed','rejected','expired','suppressed'
);

CREATE TYPE crm.project_type AS ENUM (
  'biogas_cbg','cane_yield_analytics','carbon_mrv','farm_mechanisation',
  'irrigation','input_supply','output_procurement','training_capacity',
  'credit_linkage','digital_advisory','other'
);

CREATE TYPE crm.project_stage AS ENUM (
  'identified','feasibility','proposal','approved','contracting',
  'implementation','operational','completed','on_hold','cancelled'
);

CREATE TYPE crm.pipeline_stage AS ENUM (
  'new','contacted','qualified','proposal_sent','negotiation','won','lost','dormant'
);

CREATE TYPE crm.activity_type AS ENUM (
  'call','whatsapp','email','meeting','field_visit','demo','site_survey',
  'proposal_shared','note','task','document_shared','system_event'
);

-- Billing. See INVOICE.md.
--
-- 'discarded' is for a draft that never became a document; 'cancelled' is for
-- one that did. The two are not interchangeable: a cancelled invoice keeps its
-- number forever, a discarded draft never had one.
CREATE TYPE crm.invoice_status AS ENUM (
  'draft','issued','on_hold','part_paid','paid','cancelled','discarded'
);

-- How the invoice treats tax. Captured per invoice and never inferred: the
-- historical data shows Syngenta billed with IGST while mill invoices show
-- zero against a non-zero total, and INVOICE.md §5.4 is waiting on the CA to
-- say which of those is grant disbursement rather than taxable supply.
CREATE TYPE crm.tax_treatment AS ENUM (
  'igst','cgst_sgst','zero_rated','exempt','grant'
);

-- Units a line may be billed in. Area units all carry a hectare conversion;
-- 🔴 hectares is the analysable column, the rest are input conveniences.
CREATE TYPE crm.billing_unit AS ENUM (
  'acre','sq_km','hectare','each','lump_sum','day','hour'
);

-- ---------------------------------------------------------------------
-- 2. Reference / geography  (LGD-aligned)
-- ---------------------------------------------------------------------
CREATE TABLE ref.state (
  id              smallint PRIMARY KEY,
  lgd_code        integer UNIQUE,
  name            text NOT NULL UNIQUE,
  name_local      text,
  iso_code        text,
  is_ut           boolean NOT NULL DEFAULT false
);

CREATE TABLE ref.district (
  id              integer PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  lgd_code        integer UNIQUE,
  state_id        smallint NOT NULL REFERENCES ref.state(id),
  name            text NOT NULL,
  name_local      text,
  UNIQUE (state_id, name)
);
CREATE INDEX idx_district_state ON ref.district(state_id);
CREATE INDEX idx_district_name_trgm ON ref.district USING gin (name gin_trgm_ops);

CREATE TABLE ref.block (
  id              integer PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  lgd_code        integer UNIQUE,
  district_id     integer NOT NULL REFERENCES ref.district(id),
  name            text NOT NULL,
  name_local      text,
  UNIQUE (district_id, name)
);
CREATE INDEX idx_block_district ON ref.block(district_id);

CREATE TABLE ref.village (
  id              bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  lgd_code        integer UNIQUE,
  block_id        integer REFERENCES ref.block(id),
  district_id     integer NOT NULL REFERENCES ref.district(id),
  name            text NOT NULL,
  name_local      text,
  pincode         char(6),
  latitude        numeric(9,6),
  longitude       numeric(9,6),
  -- geom         geometry(Point,4326),   -- enable with PostGIS
  UNIQUE (district_id, block_id, name)
);
CREATE INDEX idx_village_district ON ref.village(district_id);
CREATE INDEX idx_village_name_trgm ON ref.village USING gin (name gin_trgm_ops);
CREATE INDEX idx_village_pincode ON ref.village(pincode);

CREATE TABLE ref.crop (
  id              integer PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  code            text NOT NULL UNIQUE,
  name            text NOT NULL,
  name_local      text,
  category        text,               -- cereal, pulse, oilseed, cash, horticulture, fodder
  default_season  core.season
);

CREATE TABLE ref.crop_variety (
  id              integer PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  crop_id         integer NOT NULL REFERENCES ref.crop(id) ON DELETE CASCADE,
  name            text NOT NULL,
  maturity_days   smallint,
  UNIQUE (crop_id, name)
);

-- ---------------------------------------------------------------------
-- 3. Provenance & data quality (referenced by every master entity)
-- ---------------------------------------------------------------------
CREATE TABLE dq.source (
  id              integer PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  code            text NOT NULL UNIQUE,        -- 'mca_master_data', 'sfac_fpo_list', ...
  name            text NOT NULL,
  kind            dq.source_kind NOT NULL,
  url             text,
  legal_basis     text NOT NULL,               -- WHY we may hold data from this source
  licence         text,
  contains_pii    boolean NOT NULL DEFAULT false,
  is_approved     boolean NOT NULL DEFAULT false,   -- compliance sign-off
  approved_by     text,
  approved_at     timestamptz,
  refresh_cadence text,                        -- 'daily','weekly','quarterly','one_off'
  notes           text,
  created_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN dq.source.legal_basis IS
  'Mandatory. A source with no written legal basis must not be marked is_approved.';

-- Field-level provenance. Sparse by design: only fields we care to track.
CREATE TABLE dq.field_provenance (
  id              bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  entity_type     text NOT NULL,               -- 'farmer','organisation','person',...
  entity_id       uuid NOT NULL,
  field_name      text NOT NULL,
  value_text      text,                        -- the value as sourced (for contradiction review)
  source_id       integer NOT NULL REFERENCES dq.source(id),
  source_reference text,                       -- URL, file name, row no., MoU id
  confidence      numeric(3,2) NOT NULL DEFAULT 0.50 CHECK (confidence BETWEEN 0 AND 1),
  collected_at    timestamptz NOT NULL DEFAULT now(),
  verified_at     timestamptz,
  verified_by     uuid,
  is_current      boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_fieldprov_entity ON dq.field_provenance(entity_type, entity_id);
CREATE INDEX idx_fieldprov_current ON dq.field_provenance(entity_type, entity_id, field_name)
  WHERE is_current;

-- Contradictions surfaced for analyst resolution
CREATE TABLE dq.contradiction (
  id              bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  entity_type     text NOT NULL,
  entity_id       uuid NOT NULL,
  field_name      text NOT NULL,
  value_a         text,
  value_b         text,
  provenance_a    bigint REFERENCES dq.field_provenance(id),
  provenance_b    bigint REFERENCES dq.field_provenance(id),
  detected_at     timestamptz NOT NULL DEFAULT now(),
  resolved_at     timestamptz,
  resolved_by     uuid,
  resolution      text,
  CHECK (resolved_at IS NULL OR resolution IS NOT NULL)
);
CREATE INDEX idx_contradiction_open ON dq.contradiction(entity_type, entity_id)
  WHERE resolved_at IS NULL;

-- ---------------------------------------------------------------------
-- 4. Organisations
-- ---------------------------------------------------------------------
CREATE TABLE core.organisation (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  org_code            text UNIQUE,                       -- human-friendly: FPO-UP-000123
  type                core.org_type NOT NULL,
  status              core.org_status NOT NULL DEFAULT 'prospect',
  legal_form          core.legal_form NOT NULL DEFAULT 'unknown',

  name                text NOT NULL,
  name_local          text,
  short_name          text,
  aliases             text[] NOT NULL DEFAULT '{}',

  -- statutory identifiers (business data, not personal data)
  cin                 varchar(21),      -- MCA Corporate Identity Number
  registration_no     text,             -- society/co-op registration number
  registration_act    text,
  registration_date   date,
  pan_masked          varchar(14),      -- store masked only: ABCDE****F
  gstin               varchar(15),
  udyam_no            text,

  -- geography
  state_id            smallint REFERENCES ref.state(id),
  district_id         integer  REFERENCES ref.district(id),
  block_id            integer  REFERENCES ref.block(id),
  village_id          bigint   REFERENCES ref.village(id),
  address_line1       text,
  address_line2       text,
  pincode             char(6),
  latitude            numeric(9,6),
  longitude           numeric(9,6),
  -- geom             geometry(Point,4326),
  -- command_area     geometry(MultiPolygon,4326),   -- sugar mill cane command area

  website             text,
  established_year    smallint CHECK (established_year BETWEEN 1850 AND 2100),
  member_count        integer  CHECK (member_count >= 0),
  women_member_count  integer  CHECK (women_member_count >= 0),
  annual_turnover_inr numeric(16,2),
  turnover_fy         varchar(7),                        -- '2024-25'

  parent_org_id       uuid REFERENCES core.organisation(id) ON DELETE SET NULL,
  promoting_agency    text,                              -- NABARD, SFAC, NCDC, state dept
  scheme_reference    text,                              -- e.g. 10,000 FPO scheme cluster id

  quality_tier        dq.quality_tier NOT NULL DEFAULT 'bronze',
  completeness_score  smallint NOT NULL DEFAULT 0 CHECK (completeness_score BETWEEN 0 AND 100),
  primary_source_id   integer REFERENCES dq.source(id),

  owner_user_id       uuid,                              -- account owner (BD)
  tags                text[] NOT NULL DEFAULT '{}',
  extra               jsonb NOT NULL DEFAULT '{}'::jsonb,

  merged_into_id      uuid REFERENCES core.organisation(id) ON DELETE SET NULL,
  is_deleted          boolean NOT NULL DEFAULT false,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  updated_by          uuid,

  CONSTRAINT org_women_le_members CHECK (
    women_member_count IS NULL OR member_count IS NULL OR women_member_count <= member_count),
  CONSTRAINT org_not_own_parent CHECK (parent_org_id IS NULL OR parent_org_id <> id)
);

CREATE UNIQUE INDEX uq_org_cin ON core.organisation(cin) WHERE cin IS NOT NULL AND NOT is_deleted;
CREATE INDEX idx_org_type_state   ON core.organisation(type, state_id) WHERE NOT is_deleted;
CREATE INDEX idx_org_district     ON core.organisation(district_id)    WHERE NOT is_deleted;
CREATE INDEX idx_org_name_trgm    ON core.organisation USING gin (name gin_trgm_ops);
CREATE INDEX idx_org_aliases      ON core.organisation USING gin (aliases);
CREATE INDEX idx_org_tags         ON core.organisation USING gin (tags);
CREATE INDEX idx_org_extra        ON core.organisation USING gin (extra jsonb_path_ops);
CREATE INDEX idx_org_owner        ON core.organisation(owner_user_id) WHERE NOT is_deleted;
CREATE INDEX idx_org_quality      ON core.organisation(quality_tier);

-- Type extension: FPO
CREATE TABLE core.fpo_profile (
  organisation_id       uuid PRIMARY KEY REFERENCES core.organisation(id) ON DELETE CASCADE,
  authorised_capital    numeric(16,2),
  paid_up_capital       numeric(16,2),
  share_value_inr       numeric(10,2),
  shareholder_count     integer,
  business_lines        text[] NOT NULL DEFAULT '{}',   -- input sale, output aggregation, custom hiring, processing
  licences              text[] NOT NULL DEFAULT '{}',   -- seed, fertiliser, pesticide, FSSAI, mandi
  has_storage           boolean,
  storage_capacity_mt   numeric(12,2),
  has_processing_unit   boolean,
  processing_details    text,
  custom_hiring_centre  boolean,
  equity_grant_received numeric(14,2),
  credit_guarantee      boolean,
  cbbo_name             text,                            -- Cluster Based Business Organisation
  implementing_agency   text,                            -- SFAC / NABARD / NCDC / NAFED
  last_agm_date         date,
  last_annual_return_fy varchar(7),
  primary_crops         integer[] NOT NULL DEFAULT '{}'  -- ref.crop ids
);

-- Type extension: Sugar Mill
CREATE TABLE core.sugar_mill_profile (
  organisation_id           uuid PRIMARY KEY REFERENCES core.organisation(id) ON DELETE CASCADE,
  ownership                 core.mill_ownership NOT NULL DEFAULT 'private',
  crushing_capacity_tcd     integer CHECK (crushing_capacity_tcd > 0),  -- tonnes cane per day
  installed_year            smallint,
  cogeneration_mw           numeric(8,2),
  distillery_capacity_klpd  numeric(10,2),               -- kilolitres per day (ethanol)
  has_ethanol_plant         boolean NOT NULL DEFAULT false,
  has_cbg_plant             boolean NOT NULL DEFAULT false,
  refinery_capacity_tpd     integer,
  cane_command_villages     integer,                     -- count of villages in command area
  registered_cane_growers   integer,
  avg_recovery_pct          numeric(5,2) CHECK (avg_recovery_pct BETWEEN 0 AND 30),
  cane_price_srp_inr        numeric(10,2),               -- State Advised Price paid
  season_start_month        smallint CHECK (season_start_month BETWEEN 1 AND 12),
  season_end_month          smallint CHECK (season_end_month BETWEEN 1 AND 12),
  is_operational            boolean NOT NULL DEFAULT true,
  federation_membership     text[] NOT NULL DEFAULT '{}',  -- ISMA, NFCSF, state sugarfed
  cane_payment_status       text,                          -- 'current','arrears'
  cane_arrears_inr_cr       numeric(12,2)
);

-- Type extension: Cooperative society / PACS / ACS
CREATE TABLE core.cooperative_profile (
  organisation_id       uuid PRIMARY KEY REFERENCES core.organisation(id) ON DELETE CASCADE,
  society_type          text,          -- PACS, cane society, dairy, marketing, credit, multipurpose
  registration_act      text,
  affiliated_to_org_id  uuid REFERENCES core.organisation(id) ON DELETE SET NULL,
  is_pacs               boolean NOT NULL DEFAULT false,
  is_computerised       boolean,       -- PACS computerisation scheme
  deposit_base_inr      numeric(16,2),
  loan_outstanding_inr  numeric(16,2),
  area_of_operation     text,
  villages_covered      integer
);

-- Organisation-level statutory/derived yearly metrics (mill crushing, FPO turnover, etc.)
CREATE TABLE core.org_annual_metric (
  id                bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  organisation_id   uuid NOT NULL REFERENCES core.organisation(id) ON DELETE CASCADE,
  fy                varchar(7) NOT NULL,          -- '2024-25'
  metric_code       text NOT NULL,                -- 'cane_crushed_lmt','sugar_produced_lmt','turnover_inr'
  metric_value      numeric(18,4),
  unit              text,
  source_id         integer REFERENCES dq.source(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organisation_id, fy, metric_code)
);
CREATE INDEX idx_orgmetric_code ON core.org_annual_metric(metric_code, fy);

-- ---------------------------------------------------------------------
-- 5. People and their roles
-- ---------------------------------------------------------------------
CREATE TABLE core.person (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  salutation          text,
  first_name          text NOT NULL,
  middle_name         text,
  last_name           text,
  -- 🔴 Whitespace is collapsed, not just trimmed. btrim alone leaves
  -- 'Sunita  Devi' whenever middle_name is null, which is most rows — and
  -- that string is what idx_person_name_trgm indexes and what every name
  -- search compares against. A double space inside the search key is exactly
  -- the class of defect Doc 07 warns about for Indian name matching.
  full_name           text GENERATED ALWAYS AS (
                        btrim(regexp_replace(
                          coalesce(first_name,'') || ' ' ||
                          coalesce(middle_name,'') || ' ' ||
                          coalesce(last_name,''),
                          '\s+', ' ', 'g'))
                      ) STORED,
  name_local          text,
  father_or_spouse    text,                     -- essential disambiguator
  gender              core.gender,
  date_of_birth       date,
  din                 varchar(8),               -- Director Identification Number (public, MCA)
  photo_url           text,

  state_id            smallint REFERENCES ref.state(id),
  district_id         integer  REFERENCES ref.district(id),
  village_id          bigint   REFERENCES ref.village(id),

  quality_tier        dq.quality_tier NOT NULL DEFAULT 'bronze',
  primary_source_id   integer REFERENCES dq.source(id),
  is_farmer           boolean NOT NULL DEFAULT false,
  notes               text,
  extra               jsonb NOT NULL DEFAULT '{}'::jsonb,

  merged_into_id      uuid REFERENCES core.person(id) ON DELETE SET NULL,
  is_deleted          boolean NOT NULL DEFAULT false,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  updated_by          uuid
);
CREATE INDEX idx_person_name_trgm ON core.person USING gin (full_name gin_trgm_ops);
CREATE INDEX idx_person_district  ON core.person(district_id) WHERE NOT is_deleted;
CREATE UNIQUE INDEX uq_person_din ON core.person(din) WHERE din IS NOT NULL AND NOT is_deleted;

CREATE TABLE core.person_org_role (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  person_id         uuid NOT NULL REFERENCES core.person(id) ON DELETE CASCADE,
  organisation_id   uuid NOT NULL REFERENCES core.organisation(id) ON DELETE CASCADE,
  role              core.role_type NOT NULL,
  designation_text  text,                       -- free text as printed on the card
  department        text,
  is_primary_contact boolean NOT NULL DEFAULT false,
  is_decision_maker boolean NOT NULL DEFAULT false,
  valid_from        date,
  valid_to          date,
  source_id         integer REFERENCES dq.source(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);
CREATE INDEX idx_por_person ON core.person_org_role(person_id);
CREATE INDEX idx_por_org    ON core.person_org_role(organisation_id);
CREATE UNIQUE INDEX uq_por_primary ON core.person_org_role(organisation_id)
  WHERE is_primary_contact AND valid_to IS NULL;

-- ---------------------------------------------------------------------
-- 6. Contact points (phones/emails) — attached to person OR organisation
--    A contact point has its own lifecycle: verification + consent live here.
-- ---------------------------------------------------------------------
CREATE TABLE core.contact_point (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  person_id           uuid REFERENCES core.person(id) ON DELETE CASCADE,
  organisation_id     uuid REFERENCES core.organisation(id) ON DELETE CASCADE,
  kind                core.contact_kind NOT NULL,
  value_raw           text NOT NULL,
  value_normalised    text NOT NULL,            -- E.164 for phones, lowercased for email
  country_code        varchar(5) DEFAULT '+91',
  is_primary          boolean NOT NULL DEFAULT false,
  verification        core.verification_state NOT NULL DEFAULT 'unverified',
  verified_at         timestamptz,
  last_seen_valid_at  timestamptz,
  delivery_failures   smallint NOT NULL DEFAULT 0,
  bounce_reason       text,
  is_whatsapp_capable boolean,
  source_id           integer REFERENCES dq.source(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT cp_owner_exactly_one CHECK (
    (person_id IS NOT NULL)::int + (organisation_id IS NOT NULL)::int = 1)
);
CREATE INDEX idx_cp_person ON core.contact_point(person_id);
CREATE INDEX idx_cp_org    ON core.contact_point(organisation_id);
CREATE INDEX idx_cp_value  ON core.contact_point(value_normalised);
CREATE UNIQUE INDEX uq_cp_person_value ON core.contact_point(person_id, kind, value_normalised)
  WHERE person_id IS NOT NULL;
CREATE UNIQUE INDEX uq_cp_org_value ON core.contact_point(organisation_id, kind, value_normalised)
  WHERE organisation_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- 7. Farmers
--    Partitioned by state for scale (10M+ rows). Composite PK required.
-- ---------------------------------------------------------------------
CREATE TABLE core.farmer (
  id                    uuid NOT NULL DEFAULT uuid_generate_v4(),
  state_id              smallint NOT NULL REFERENCES ref.state(id),
  farmer_code           text,                       -- FRM-UP-00001234
  person_id             uuid REFERENCES core.person(id) ON DELETE SET NULL,

  first_name            text NOT NULL,
  last_name             text,
  name_local            text,
  father_or_spouse      text,
  gender                core.gender,
  date_of_birth         date,
  age_band              text,                       -- '18-25','26-35',... when DOB unknown
  social_category       core.social_category NOT NULL DEFAULT 'undisclosed',

  district_id           integer REFERENCES ref.district(id),
  block_id              integer REFERENCES ref.block(id),
  village_id            bigint  REFERENCES ref.village(id),
  address_line          text,
  pincode               char(6),
  latitude              numeric(9,6),
  longitude             numeric(9,6),

  total_area_ha         numeric(10,4) CHECK (total_area_ha >= 0),
  area_source           text,                       -- 'self_declared','document','satellite'
  farmer_class          core.farmer_class NOT NULL DEFAULT 'unknown',
  irrigated_area_ha     numeric(10,4),
  primary_crop_id       integer REFERENCES ref.crop(id),

  -- identity references: NEVER store plaintext government IDs
  aadhaar_hash          bytea,                      -- sha256(aadhaar || per-record salt)
  aadhaar_last4         char(4),
  agristack_farmer_id   text,                       -- if the farmer shares it
  kisan_credit_card     boolean,
  has_bank_account      boolean,
  has_upi               boolean,

  -- relationships (denormalised pointers; full detail in link tables)
  primary_fpo_id        uuid REFERENCES core.organisation(id) ON DELETE SET NULL,
  supplying_mill_id     uuid REFERENCES core.organisation(id) ON DELETE SET NULL,
  mill_supplier_code    text,                       -- cane grower code at the mill

  theta_external_id     text,                       -- link to Theta Analytics records
  quality_tier          dq.quality_tier NOT NULL DEFAULT 'bronze',
  completeness_score    smallint NOT NULL DEFAULT 0 CHECK (completeness_score BETWEEN 0 AND 100),
  primary_source_id     integer REFERENCES dq.source(id),
  consent_summary       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- cached; ledger is authoritative

  owner_user_id         uuid,
  tags                  text[] NOT NULL DEFAULT '{}',
  extra                 jsonb NOT NULL DEFAULT '{}'::jsonb,

  merged_into_id        uuid,
  is_deleted            boolean NOT NULL DEFAULT false,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  created_by            uuid,
  updated_by            uuid,

  PRIMARY KEY (id, state_id),
  CONSTRAINT farmer_irrigated_le_total CHECK (
    irrigated_area_ha IS NULL OR total_area_ha IS NULL OR irrigated_area_ha <= total_area_ha)
) PARTITION BY LIST (state_id);

-- Partitions are created per state; see seed_reference.sql for the generator.
-- Example:
CREATE TABLE core.farmer_p_default PARTITION OF core.farmer DEFAULT;

CREATE INDEX idx_farmer_district  ON core.farmer(district_id);
CREATE INDEX idx_farmer_village   ON core.farmer(village_id);
CREATE INDEX idx_farmer_fpo       ON core.farmer(primary_fpo_id);
CREATE INDEX idx_farmer_mill      ON core.farmer(supplying_mill_id);
CREATE INDEX idx_farmer_name_trgm ON core.farmer USING gin (first_name gin_trgm_ops);
CREATE INDEX idx_farmer_quality   ON core.farmer(quality_tier);
CREATE INDEX idx_farmer_theta     ON core.farmer(theta_external_id);
CREATE INDEX idx_farmer_tags      ON core.farmer USING gin (tags);

CREATE TABLE core.land_parcel (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  farmer_id           uuid NOT NULL,
  farmer_state_id     smallint NOT NULL,
  survey_number       text,
  khasra_number       text,
  khata_number        text,
  area_ha             numeric(10,4) NOT NULL CHECK (area_ha > 0),
  tenure              core.land_tenure NOT NULL DEFAULT 'unknown',
  irrigation          core.irrigation_source NOT NULL DEFAULT 'unknown',
  soil_type           text,
  village_id          bigint REFERENCES ref.village(id),
  latitude            numeric(9,6),
  longitude           numeric(9,6),
  -- boundary         geometry(Polygon,4326),
  area_verified       boolean NOT NULL DEFAULT false,
  verification_method text,                       -- 'land_record','gps_walk','satellite'
  source_id           integer REFERENCES dq.source(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (farmer_id, farmer_state_id) REFERENCES core.farmer(id, state_id) ON DELETE CASCADE
);
CREATE INDEX idx_parcel_farmer ON core.land_parcel(farmer_id, farmer_state_id);
CREATE INDEX idx_parcel_village ON core.land_parcel(village_id);

CREATE TABLE core.farmer_crop (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  farmer_id         uuid NOT NULL,
  farmer_state_id   smallint NOT NULL,
  crop_id           integer NOT NULL REFERENCES ref.crop(id),
  variety_id        integer REFERENCES ref.crop_variety(id),
  season            core.season NOT NULL,
  crop_year         smallint NOT NULL CHECK (crop_year BETWEEN 1990 AND 2100),
  area_ha           numeric(10,4) CHECK (area_ha >= 0),
  sowing_date       date,
  expected_yield_mt numeric(12,3),
  actual_yield_mt   numeric(12,3),
  sold_to_org_id    uuid REFERENCES core.organisation(id) ON DELETE SET NULL,
  price_per_qtl_inr numeric(10,2),
  source_id         integer REFERENCES dq.source(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (farmer_id, farmer_state_id) REFERENCES core.farmer(id, state_id) ON DELETE CASCADE,
  UNIQUE (farmer_id, farmer_state_id, crop_id, season, crop_year)
);
CREATE INDEX idx_fcrop_crop_year ON core.farmer_crop(crop_id, crop_year, season);

CREATE TABLE core.farmer_livestock (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  farmer_id         uuid NOT NULL,
  farmer_state_id   smallint NOT NULL,
  animal_type       text NOT NULL,               -- cattle, buffalo, goat, poultry
  head_count        integer NOT NULL CHECK (head_count >= 0),
  recorded_on       date NOT NULL DEFAULT current_date,
  FOREIGN KEY (farmer_id, farmer_state_id) REFERENCES core.farmer(id, state_id) ON DELETE CASCADE
);

-- Farmer ↔ organisation membership (FPO member, society member, mill supplier)
CREATE TABLE core.farmer_org_link (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  farmer_id         uuid NOT NULL,
  farmer_state_id   smallint NOT NULL,
  organisation_id   uuid NOT NULL REFERENCES core.organisation(id) ON DELETE CASCADE,
  relationship      text NOT NULL,               -- 'fpo_member','mill_supplier','society_member','borrower'
  member_code       text,
  shares_held       integer,
  joined_on         date,
  left_on           date,
  is_active         boolean NOT NULL DEFAULT true,
  source_id         integer REFERENCES dq.source(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (farmer_id, farmer_state_id) REFERENCES core.farmer(id, state_id) ON DELETE CASCADE,
  UNIQUE (farmer_id, farmer_state_id, organisation_id, relationship)
);
CREATE INDEX idx_fol_org ON core.farmer_org_link(organisation_id) WHERE is_active;

-- Sugar mill cane command area at village granularity
CREATE TABLE core.mill_command_village (
  id                bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  mill_org_id       uuid NOT NULL REFERENCES core.organisation(id) ON DELETE CASCADE,
  village_id        bigint NOT NULL REFERENCES ref.village(id) ON DELETE CASCADE,
  distance_km       numeric(6,2),
  registered_growers integer,
  cane_area_ha      numeric(12,3),
  season_fy         varchar(7),
  source_id         integer REFERENCES dq.source(id),
  UNIQUE (mill_org_id, village_id, season_fy)
);
CREATE INDEX idx_mcv_village ON core.mill_command_village(village_id);

-- ---------------------------------------------------------------------
-- 8. Consent & communication
-- ---------------------------------------------------------------------
-- Append-only ledger. Never UPDATE a row here; insert a new one.
CREATE TABLE comm.consent_event (
  id                bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  subject_type      text NOT NULL,                -- 'farmer','person'
  subject_id        uuid NOT NULL,
  contact_point_id  uuid REFERENCES core.contact_point(id) ON DELETE SET NULL,
  channel           comm.channel NOT NULL,
  purpose           comm.consent_purpose NOT NULL,
  status            comm.consent_status NOT NULL,
  evidence_type     text NOT NULL,                -- 'signed_form','in_app_checkbox','whatsapp_optin',
                                                  -- 'ivr_confirmation','mou_clause','sms_keyword'
  evidence_ref      text,                         -- document id, form id, message id
  evidence_url      text,
  captured_by       uuid,
  captured_at       timestamptz NOT NULL DEFAULT now(),
  ip_address        inet,
  device_id         text,
  language          varchar(8) NOT NULL DEFAULT 'hi',
  notice_version    text NOT NULL,                -- version of the privacy notice shown
  expires_at        timestamptz,
  notes             text
);
CREATE INDEX idx_consent_subject ON comm.consent_event(subject_type, subject_id, channel, purpose, captured_at DESC);
COMMENT ON TABLE comm.consent_event IS
  'Append-only. The current consent state is the latest row per (subject, channel, purpose). Never delete or update.';

-- Materialised current state for fast segment queries. Refreshed by trigger.
CREATE TABLE comm.consent_current (
  subject_type      text NOT NULL,
  subject_id        uuid NOT NULL,
  channel           comm.channel NOT NULL,
  purpose           comm.consent_purpose NOT NULL,
  status            comm.consent_status NOT NULL,
  effective_at      timestamptz NOT NULL,
  expires_at        timestamptz,
  last_event_id     bigint NOT NULL REFERENCES comm.consent_event(id),
  PRIMARY KEY (subject_type, subject_id, channel, purpose)
);
CREATE INDEX idx_consent_current_optedin ON comm.consent_current(channel, purpose)
  WHERE status = 'opted_in';

-- Hard suppression: survives re-import, wins over everything.
CREATE TABLE comm.suppression (
  id                bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  value_normalised  text NOT NULL,                -- phone E.164 or email
  channel           comm.channel,                 -- NULL = all channels
  reason            text NOT NULL,                -- 'user_optout','complaint','hard_bounce','legal_request'
  suppressed_at     timestamptz NOT NULL DEFAULT now(),
  suppressed_by     uuid,
  notes             text,
  UNIQUE (value_normalised, channel)
);
CREATE INDEX idx_suppression_value ON comm.suppression(value_normalised);

CREATE TABLE comm.template (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  channel           comm.channel NOT NULL,
  code              text NOT NULL,
  name              text NOT NULL,
  language          varchar(8) NOT NULL DEFAULT 'en',
  category          text,                         -- MARKETING / UTILITY / AUTHENTICATION
  body              text NOT NULL,
  header            text,
  footer            text,
  variables         jsonb NOT NULL DEFAULT '[]'::jsonb,
  provider_template_id text,                      -- Meta template id
  approval_status   text NOT NULL DEFAULT 'draft', -- draft/pending/approved/rejected/paused
  approval_notes    text,
  is_active         boolean NOT NULL DEFAULT false,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (channel, code, language)
);

CREATE TABLE comm.campaign (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  name              text NOT NULL,
  channel           comm.channel NOT NULL,
  purpose           comm.consent_purpose NOT NULL,
  template_id       uuid REFERENCES comm.template(id),
  segment_definition jsonb NOT NULL,              -- serialised filter tree
  scheduled_at      timestamptz,
  started_at        timestamptz,
  completed_at      timestamptz,
  status            text NOT NULL DEFAULT 'draft', -- draft/scheduled/running/paused/completed/cancelled
  total_targeted    integer NOT NULL DEFAULT 0,
  total_eligible    integer NOT NULL DEFAULT 0,   -- after consent + suppression filter
  total_sent        integer NOT NULL DEFAULT 0,
  total_delivered   integer NOT NULL DEFAULT 0,
  total_read        integer NOT NULL DEFAULT 0,
  total_failed      integer NOT NULL DEFAULT 0,
  total_optout      integer NOT NULL DEFAULT 0,
  estimated_cost_inr numeric(12,2),
  actual_cost_inr   numeric(12,2),
  approved_by       uuid,
  approved_at       timestamptz,
  created_by        uuid,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_campaign_status ON comm.campaign(status, scheduled_at);

-- High-volume: partitioned monthly by sent_at
CREATE TABLE comm.message (
  id                uuid NOT NULL DEFAULT uuid_generate_v4(),
  campaign_id       uuid,
  template_id       uuid,
  channel           comm.channel NOT NULL,
  subject_type      text,
  subject_id        uuid,
  contact_point_id  uuid,
  to_value          text NOT NULL,
  body_rendered     text,
  state             comm.message_state NOT NULL DEFAULT 'queued',
  provider          text,                         -- 'meta_cloud','ses'
  provider_message_id text,
  error_code        text,
  error_message     text,
  cost_inr          numeric(10,4),
  queued_at         timestamptz NOT NULL DEFAULT now(),
  sent_at           timestamptz NOT NULL DEFAULT now(),
  delivered_at      timestamptz,
  read_at           timestamptz,
  failed_at         timestamptz,
  PRIMARY KEY (id, sent_at)
) PARTITION BY RANGE (sent_at);

CREATE TABLE comm.message_p_default PARTITION OF comm.message DEFAULT;
CREATE INDEX idx_message_subject  ON comm.message(subject_type, subject_id, sent_at DESC);
CREATE INDEX idx_message_campaign ON comm.message(campaign_id);
CREATE INDEX idx_message_provider ON comm.message(provider_message_id);
CREATE INDEX idx_message_state    ON comm.message(state, sent_at DESC);

CREATE TABLE comm.inbound_message (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  channel           comm.channel NOT NULL,
  from_value        text NOT NULL,
  body              text,
  provider_message_id text,
  matched_subject_type text,
  matched_subject_id uuid,
  intent            text,                        -- 'optout','optin','query','other'
  handled           boolean NOT NULL DEFAULT false,
  received_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_inbound_from ON comm.inbound_message(from_value, received_at DESC);
CREATE INDEX idx_inbound_unhandled ON comm.inbound_message(received_at) WHERE NOT handled;

-- ---------------------------------------------------------------------
-- 9. CRM: projects, pipeline, agents, activities
-- ---------------------------------------------------------------------
CREATE TABLE crm.project (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_code        text NOT NULL UNIQUE,       -- PRJ-2026-0042
  name                text NOT NULL,
  type                crm.project_type NOT NULL,
  stage               crm.project_stage NOT NULL DEFAULT 'identified',
  description         text,

  lead_org_id         uuid REFERENCES core.organisation(id) ON DELETE SET NULL,
  opportunity_id      uuid,                       -- originating BD opportunity

  value_inr           numeric(16,2),
  currency            char(3) NOT NULL DEFAULT 'INR',
  funding_source      text,
  start_date          date,
  expected_end_date   date,
  actual_end_date     date,

  state_id            smallint REFERENCES ref.state(id),
  district_id         integer REFERENCES ref.district(id),

  manager_user_id     uuid,
  health              text NOT NULL DEFAULT 'green',   -- green/amber/red
  health_note         text,
  farmers_impacted    integer,
  area_covered_ha     numeric(14,3),

  tags                text[] NOT NULL DEFAULT '{}',
  extra               jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_deleted          boolean NOT NULL DEFAULT false,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  CHECK (expected_end_date IS NULL OR start_date IS NULL OR expected_end_date >= start_date)
);
CREATE INDEX idx_project_stage ON crm.project(stage) WHERE NOT is_deleted;
CREATE INDEX idx_project_org   ON crm.project(lead_org_id);
CREATE INDEX idx_project_mgr   ON crm.project(manager_user_id);

CREATE TABLE crm.project_organisation (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id        uuid NOT NULL REFERENCES crm.project(id) ON DELETE CASCADE,
  organisation_id   uuid NOT NULL REFERENCES core.organisation(id) ON DELETE CASCADE,
  role              text NOT NULL,               -- client, partner, funder, aggregator, vendor
  is_primary        boolean NOT NULL DEFAULT false,
  UNIQUE (project_id, organisation_id, role)
);

CREATE TABLE crm.project_contact (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id        uuid NOT NULL REFERENCES crm.project(id) ON DELETE CASCADE,
  person_id         uuid NOT NULL REFERENCES core.person(id) ON DELETE CASCADE,
  role_on_project   text NOT NULL,               -- sponsor, day-to-day, technical, finance
  is_primary        boolean NOT NULL DEFAULT false,
  UNIQUE (project_id, person_id, role_on_project)
);

CREATE TABLE crm.project_site (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id        uuid NOT NULL REFERENCES crm.project(id) ON DELETE CASCADE,
  village_id        bigint REFERENCES ref.village(id),
  organisation_id   uuid REFERENCES core.organisation(id) ON DELETE SET NULL,
  site_name         text,
  latitude          numeric(9,6),
  longitude         numeric(9,6),
  status            text NOT NULL DEFAULT 'planned'
);
CREATE INDEX idx_psite_project ON crm.project_site(project_id);

CREATE TABLE crm.project_milestone (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  project_id        uuid NOT NULL REFERENCES crm.project(id) ON DELETE CASCADE,
  name              text NOT NULL,
  due_date          date,
  completed_date    date,
  owner_user_id     uuid,
  status            text NOT NULL DEFAULT 'pending',
  sort_order        smallint NOT NULL DEFAULT 0
);
CREATE INDEX idx_milestone_project ON crm.project_milestone(project_id, sort_order);

-- Business Development pipeline
CREATE TABLE crm.lead (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  lead_code         text UNIQUE,
  source            text NOT NULL,               -- referral, campaign, field, inbound, event, list
  source_detail     text,
  organisation_id   uuid REFERENCES core.organisation(id) ON DELETE SET NULL,
  org_name_raw      text,                        -- before org record exists
  person_id         uuid REFERENCES core.person(id) ON DELETE SET NULL,
  contact_name_raw  text,
  contact_phone     text,
  contact_email     text,
  state_id          smallint REFERENCES ref.state(id),
  district_id       integer REFERENCES ref.district(id),
  interest_type     crm.project_type,
  notes             text,
  owner_user_id     uuid,
  status            text NOT NULL DEFAULT 'new', -- new/working/converted/disqualified
  disqualify_reason text,
  converted_opportunity_id uuid,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_lead_owner ON crm.lead(owner_user_id, status);
CREATE INDEX idx_lead_org   ON crm.lead(organisation_id);

CREATE TABLE crm.opportunity (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  opp_code            text UNIQUE,
  name                text NOT NULL,
  organisation_id     uuid NOT NULL REFERENCES core.organisation(id) ON DELETE CASCADE,
  primary_contact_id  uuid REFERENCES core.person(id) ON DELETE SET NULL,
  type                crm.project_type,
  stage               crm.pipeline_stage NOT NULL DEFAULT 'new',
  probability_pct     smallint NOT NULL DEFAULT 10 CHECK (probability_pct BETWEEN 0 AND 100),
  value_inr           numeric(16,2) NOT NULL DEFAULT 0,
  weighted_value_inr  numeric(16,2) GENERATED ALWAYS AS
                        (value_inr * probability_pct / 100.0) STORED,
  expected_close_date date,
  actual_close_date   date,
  owner_user_id       uuid NOT NULL,
  stage_entered_at    timestamptz NOT NULL DEFAULT now(),
  loss_reason         text,
  competitor          text,
  next_step           text,
  next_step_due       date,
  project_id          uuid REFERENCES crm.project(id) ON DELETE SET NULL,
  tags                text[] NOT NULL DEFAULT '{}',
  is_deleted          boolean NOT NULL DEFAULT false,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CHECK (stage <> 'lost' OR loss_reason IS NOT NULL)
);
CREATE INDEX idx_opp_stage ON crm.opportunity(stage, expected_close_date) WHERE NOT is_deleted;
CREATE INDEX idx_opp_owner ON crm.opportunity(owner_user_id, stage) WHERE NOT is_deleted;
CREATE INDEX idx_opp_org   ON crm.opportunity(organisation_id);
CREATE INDEX idx_opp_ageing ON crm.opportunity(stage_entered_at) WHERE NOT is_deleted;

CREATE TABLE crm.opportunity_stage_history (
  id                bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  opportunity_id    uuid NOT NULL REFERENCES crm.opportunity(id) ON DELETE CASCADE,
  from_stage        crm.pipeline_stage,
  to_stage          crm.pipeline_stage NOT NULL,
  changed_by        uuid,
  changed_at        timestamptz NOT NULL DEFAULT now(),
  days_in_from_stage integer,
  note              text
);
CREATE INDEX idx_oppstage_opp ON crm.opportunity_stage_history(opportunity_id, changed_at DESC);

-- Agents
CREATE TABLE crm.agent (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id           uuid NOT NULL UNIQUE,
  employee_code     text UNIQUE,
  full_name         text NOT NULL,
  phone             text,
  email             text,
  reports_to_id     uuid REFERENCES crm.agent(id) ON DELETE SET NULL,
  designation       text,
  date_joined       date,
  is_active         boolean NOT NULL DEFAULT true,
  base_district_id  integer REFERENCES ref.district(id),
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE crm.agent_territory (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  agent_id          uuid NOT NULL REFERENCES crm.agent(id) ON DELETE CASCADE,
  state_id          smallint REFERENCES ref.state(id),
  district_id       integer REFERENCES ref.district(id),
  block_id          integer REFERENCES ref.block(id),
  valid_from        date NOT NULL DEFAULT current_date,
  valid_to          date,
  CHECK (state_id IS NOT NULL OR district_id IS NOT NULL OR block_id IS NOT NULL)
);
CREATE INDEX idx_territory_agent ON crm.agent_territory(agent_id) WHERE valid_to IS NULL;
CREATE INDEX idx_territory_district ON crm.agent_territory(district_id) WHERE valid_to IS NULL;

CREATE TABLE crm.agent_target (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  agent_id          uuid NOT NULL REFERENCES crm.agent(id) ON DELETE CASCADE,
  period_start      date NOT NULL,
  period_end        date NOT NULL,
  metric_code       text NOT NULL,               -- visits, new_orgs, consented_farmers, pipeline_value
  target_value      numeric(14,2) NOT NULL,
  achieved_value    numeric(14,2) NOT NULL DEFAULT 0,
  UNIQUE (agent_id, period_start, metric_code),
  CHECK (period_end >= period_start)
);

CREATE TABLE crm.field_visit (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_uuid       uuid NOT NULL UNIQUE,        -- generated on device, guarantees idempotent sync
  agent_id          uuid NOT NULL REFERENCES crm.agent(id) ON DELETE CASCADE,
  organisation_id   uuid REFERENCES core.organisation(id) ON DELETE SET NULL,
  farmer_id         uuid,
  farmer_state_id   smallint,
  person_id         uuid REFERENCES core.person(id) ON DELETE SET NULL,
  visit_purpose     text NOT NULL,
  outcome           text,
  notes             text,
  next_action       text,
  next_action_due   date,
  latitude          numeric(9,6),
  longitude         numeric(9,6),
  gps_accuracy_m    numeric(8,2),
  visited_at        timestamptz NOT NULL,
  device_recorded_at timestamptz,
  synced_at         timestamptz NOT NULL DEFAULT now(),
  photo_urls        text[] NOT NULL DEFAULT '{}',
  created_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (farmer_id, farmer_state_id) REFERENCES core.farmer(id, state_id) ON DELETE SET NULL
);
CREATE INDEX idx_visit_agent_date ON crm.field_visit(agent_id, visited_at DESC);
CREATE INDEX idx_visit_org ON crm.field_visit(organisation_id);

-- Universal activity feed (polymorphic)
CREATE TABLE crm.activity (
  id                bigint NOT NULL GENERATED BY DEFAULT AS IDENTITY,
  type              crm.activity_type NOT NULL,
  subject_type      text NOT NULL,               -- organisation/person/farmer/project/opportunity/lead
  subject_id        uuid NOT NULL,
  title             text NOT NULL,
  body              text,
  direction         text,                        -- inbound/outbound/internal
  actor_user_id     uuid,
  occurred_at       timestamptz NOT NULL DEFAULT now(),
  duration_minutes  smallint,
  outcome           text,
  related_message_id uuid,
  metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE crm.activity_p_default PARTITION OF crm.activity DEFAULT;
CREATE INDEX idx_activity_subject ON crm.activity(subject_type, subject_id, occurred_at DESC);
CREATE INDEX idx_activity_actor   ON crm.activity(actor_user_id, occurred_at DESC);

CREATE TABLE crm.task (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  title             text NOT NULL,
  description       text,
  subject_type      text,
  subject_id        uuid,
  assigned_to       uuid,
  due_at            timestamptz,
  priority          smallint NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
  status            text NOT NULL DEFAULT 'open',  -- open/in_progress/done/cancelled
  completed_at      timestamptz,
  auto_generated    boolean NOT NULL DEFAULT false,
  rule_code         text,                          -- which automation created it
  created_by        uuid,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_task_assignee ON crm.task(assigned_to, status, due_at);

-- ---------------------------------------------------------------------
-- 9b. Billing   (INVOICE.md)
--
-- The register and the document, not the ledger. This schema records what was
-- billed, to whom, for which project, and whether it was paid. It does not
-- file a return, compute TDS or hold a trial balance — those stay in Tally.
-- ---------------------------------------------------------------------

-- Who is issuing. Two rows today: TFD and TEPL.
--
-- 🔴 Versioned, not mutable. TEPL's bank moved from Axis to ICICI during
-- FY2026-27, so a 2025 invoice re-rendered today must print the Axis block it
-- was issued with. Closing a row and opening a new one is the only correct way
-- to change an address, a bank or a signatory.
CREATE TABLE crm.billing_entity (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  code              text NOT NULL,              -- TFD, TEPL
  legal_name        text NOT NULL,
  address_lines     text[] NOT NULL DEFAULT '{}',
  state_id          smallint REFERENCES ref.state(id),
  state_code        char(2) NOT NULL,           -- GST state code, '07' for Delhi
  gstin             text,
  pan               text,
  contact_name      text,
  contact_phone     text,
  contact_email     text,

  bank_name         text,
  bank_account_no   text,
  bank_ifsc         text,
  bank_branch       text,
  bank_address      text,

  signatory_name    text,
  signatory_title   text,
  declaration       text,
  jurisdiction_note text,

  template_code     text NOT NULL DEFAULT 'T2', -- T1 / T2 / T3, see INVOICE.md §2.4
  logo_storage_key  text,

  valid_from        date NOT NULL,
  valid_to          date,                       -- NULL = current
  created_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
-- One current row per code. A closed row may overlap nothing, which the
-- exclusion constraint below enforces properly.
CREATE UNIQUE INDEX idx_billing_entity_current
  ON crm.billing_entity(code) WHERE valid_to IS NULL;
ALTER TABLE crm.billing_entity ADD CONSTRAINT billing_entity_no_overlap
  EXCLUDE USING gist (
    code WITH =,
    daterange(valid_from, COALESCE(valid_to, 'infinity'::date), '[]') WITH &&
  );

-- Number allocation. 🔴 Gapless and single-use: cancelling an invoice keeps
-- its number and the series moves on. The FY26 data reissued TEPL/2026-27/03
-- and /04 after cancelling them, which is what this table exists to prevent.
CREATE TABLE crm.invoice_number_series (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  entity_code       text NOT NULL,              -- TFD / TEPL, not the entity id: the
                                                -- series outlives any one entity version
  financial_year    text NOT NULL,              -- '2026-27'
  stream            text NOT NULL DEFAULT '',   -- 'M' for Mizoram, '' for the main run
  pattern           text NOT NULL DEFAULT '{entity}/{fy}/{stream}{n}',
  next_number       integer NOT NULL DEFAULT 1 CHECK (next_number >= 1),
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_code, financial_year, stream)
);

CREATE TABLE crm.invoice (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),

  -- NULL while draft. Assigned once, at issue, and never reassigned.
  invoice_no          text,
  financial_year      text,
  stream              text NOT NULL DEFAULT '',

  billing_entity_id   uuid NOT NULL REFERENCES crm.billing_entity(id),
  entity_code         text NOT NULL,

  -- The join that makes this a CRM feature rather than a billing app.
  organisation_id     uuid REFERENCES core.organisation(id) ON DELETE RESTRICT,
  project_id          uuid REFERENCES crm.project(id) ON DELETE SET NULL,

  invoice_date        date NOT NULL,
  due_date            date,

  -- 🔴 Snapshotted at issue. A customer who later moves office must not
  -- silently rewrite a document their accounts team already holds.
  buyer_name          text NOT NULL,
  buyer_address       text,
  buyer_gstin         text,
  buyer_pan           text,
  buyer_state_code    char(2),
  buyer_is_govt_uin   boolean NOT NULL DEFAULT false,

  -- Ship-to, printed only by template T3.
  consignee_name      text,
  consignee_address   text,
  consignee_gstin     text,

  buyer_order_no      text,                     -- Syngenta PO, e.g. 1100644669
  buyer_order_date    date,
  work_order_ref      text,                     -- Mizoram
  letter_ref          text,
  delivery_note       text,
  payment_terms       text,
  destination         text,
  data_link_url       text,                     -- deliverable handover

  place_of_supply_state_code char(2),
  tax_treatment       crm.tax_treatment NOT NULL DEFAULT 'igst',
  tax_rate_pct        numeric(5,2) NOT NULL DEFAULT 18.00,

  -- Totals are written by trigger from the lines. Storing them keeps the
  -- register listable without a join to a sum, and the trigger keeps them
  -- honest.
  taxable_value       numeric(14,2) NOT NULL DEFAULT 0,
  tax_amount          numeric(14,2) NOT NULL DEFAULT 0,
  total_value         numeric(14,2) NOT NULL DEFAULT 0,
  amount_in_words     text,

  status              crm.invoice_status NOT NULL DEFAULT 'draft',
  issued_at           timestamptz,
  cancelled_at        timestamptz,
  cancellation_reason text,
  held_at             timestamptz,
  hold_reason         text,

  -- The rendered document. sha256 is what lets you prove the PDF you hold is
  -- the PDF you sent.
  pdf_storage_key     text,
  pdf_sha256          bytea,
  pdf_generated_at    timestamptz,
  template_code       text NOT NULL DEFAULT 'T2',

  -- Where this record came from. A historical import is locked: regenerating
  -- it must reproduce the original document, not re-render it in a template
  -- that has since changed.
  source_id           integer REFERENCES dq.source(id),
  is_historical       boolean NOT NULL DEFAULT false,
  extraction_id       uuid,                     -- crm.invoice_extraction

  notes               text,
  extra               jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_deleted          boolean NOT NULL DEFAULT false,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  created_by          uuid,
  updated_by          uuid,

  -- A document exists or it does not. These four move together.
  CHECK (status = 'draft' OR status = 'discarded' OR invoice_no IS NOT NULL),
  CHECK (status <> 'cancelled' OR cancellation_reason IS NOT NULL),
  CHECK (status <> 'on_hold'  OR hold_reason IS NOT NULL),
  CHECK (due_date IS NULL OR due_date >= invoice_date)
);

-- 🔴 The constraint that makes D3 impossible. Partial, because drafts have no
-- number and many of them may sit at NULL at once.
CREATE UNIQUE INDEX idx_invoice_no_unique
  ON crm.invoice(entity_code, invoice_no) WHERE invoice_no IS NOT NULL;
CREATE INDEX idx_invoice_org    ON crm.invoice(organisation_id) WHERE NOT is_deleted;
CREATE INDEX idx_invoice_status ON crm.invoice(status, invoice_date DESC) WHERE NOT is_deleted;
CREATE INDEX idx_invoice_fy     ON crm.invoice(entity_code, financial_year) WHERE NOT is_deleted;
CREATE INDEX idx_invoice_project ON crm.invoice(project_id) WHERE project_id IS NOT NULL;

CREATE TABLE crm.invoice_line (
  id                  uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id          uuid NOT NULL REFERENCES crm.invoice(id) ON DELETE CASCADE,
  line_no             smallint NOT NULL,
  description         text NOT NULL,
  hsn_sac             text,

  quantity            numeric(14,4) NOT NULL,
  unit                crm.billing_unit NOT NULL,

  -- 🔴 CLAUDE.md: all area in hectares. Acres and square kilometres are input
  -- conveniences converted at the edge, and this column is the conversion —
  -- generated, so it cannot drift from the quantity it derives from.
  --   1 acre  = 0.40468564224 ha exactly
  --   1 sq km = 100 ha exactly
  quantity_ha         numeric(14,4) GENERATED ALWAYS AS (
                        CASE unit
                          WHEN 'acre'    THEN quantity * 0.40468564224
                          WHEN 'sq_km'   THEN quantity * 100
                          WHEN 'hectare' THEN quantity
                          ELSE NULL
                        END
                      ) STORED,

  rate                numeric(14,4) NOT NULL,
  -- The Mizoram survey work is quoted at a rate that already contains GST,
  -- while spraying is quoted ex-tax. Without this flag the register overstates
  -- revenue on every survey invoice.
  rate_is_tax_inclusive boolean NOT NULL DEFAULT false,
  tax_rate_pct        numeric(5,2) NOT NULL DEFAULT 18.00,

  line_taxable_value  numeric(14,2) NOT NULL,
  line_tax_amount     numeric(14,2) NOT NULL DEFAULT 0,
  line_total          numeric(14,2) NOT NULL,

  district_id         integer REFERENCES ref.district(id),
  state_id            smallint REFERENCES ref.state(id),
  location_note       text,                     -- 'Keifang, Saitual'

  UNIQUE (invoice_id, line_no),
  CHECK (quantity > 0),
  CHECK (rate >= 0)
);
CREATE INDEX idx_invline_invoice  ON crm.invoice_line(invoice_id, line_no);
CREATE INDEX idx_invline_district ON crm.invoice_line(district_id) WHERE district_id IS NOT NULL;
CREATE INDEX idx_invline_hsn      ON crm.invoice_line(hsn_sac);

CREATE TABLE crm.invoice_payment (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id        uuid NOT NULL REFERENCES crm.invoice(id) ON DELETE CASCADE,
  received_on       date NOT NULL,
  amount            numeric(14,2) NOT NULL CHECK (amount > 0),
  mode              text,                       -- rtgs / neft / cheque / upi
  reference         text,
  note              text,
  recorded_by       uuid,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_invpay_invoice ON crm.invoice_payment(invoice_id, received_on);

-- What the extraction agent read off an uploaded document, kept beside what a
-- human then accepted. Provenance for a machine-filled form: if the model
-- misreads a rate, the evidence of what it read is still here afterwards.
CREATE TABLE crm.invoice_extraction (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id        uuid REFERENCES crm.invoice(id) ON DELETE SET NULL,
  file_name         text NOT NULL,
  storage_key       text,
  mime_type         text,
  size_bytes        bigint,
  sha256            bytea,

  model             text,                       -- claude-opus-5
  status            text NOT NULL DEFAULT 'pending', -- pending/succeeded/failed
  error             text,
  raw_response      jsonb,                      -- exactly what came back
  extracted         jsonb NOT NULL DEFAULT '{}'::jsonb,
  field_confidence  jsonb NOT NULL DEFAULT '{}'::jsonb,
  warnings          text[] NOT NULL DEFAULT '{}',

  -- Set when a human accepts the draft, so "what the model said" and "what was
  -- billed" can always be compared.
  accepted_at       timestamptz,
  accepted_by       uuid,

  duration_ms       integer,
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        uuid
);
CREATE INDEX idx_invextract_invoice ON crm.invoice_extraction(invoice_id);
CREATE INDEX idx_invextract_status  ON crm.invoice_extraction(status, created_at DESC);

-- ---------------------------------------------------------------------
-- 10. Documents
-- ---------------------------------------------------------------------
CREATE TABLE core.document (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  subject_type      text NOT NULL,
  subject_id        uuid NOT NULL,
  doc_type          text NOT NULL,              -- registration_certificate, mou, proposal, consent_form
  file_name         text NOT NULL,
  storage_key       text NOT NULL,             -- S3 object key
  mime_type         text,
  size_bytes        bigint,
  sha256            bytea,
  contains_pii      boolean NOT NULL DEFAULT false,
  uploaded_by       uuid,
  uploaded_at       timestamptz NOT NULL DEFAULT now(),
  retention_until   date
);
CREATE INDEX idx_document_subject ON core.document(subject_type, subject_id);

-- ---------------------------------------------------------------------
-- 11. Import / dedupe machinery
-- ---------------------------------------------------------------------
CREATE TABLE dq.import_batch (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  file_name         text NOT NULL,
  storage_key       text,
  entity_type       text NOT NULL,
  source_id         integer NOT NULL REFERENCES dq.source(id),
  mapping           jsonb NOT NULL DEFAULT '{}'::jsonb,
  status            text NOT NULL DEFAULT 'uploaded', -- uploaded/validating/dry_run/committed/failed/rolled_back
  rows_total        integer NOT NULL DEFAULT 0,
  rows_created      integer NOT NULL DEFAULT 0,
  rows_updated      integer NOT NULL DEFAULT 0,
  rows_skipped      integer NOT NULL DEFAULT 0,
  rows_error        integer NOT NULL DEFAULT 0,
  legal_basis_confirmed boolean NOT NULL DEFAULT false,
  consent_evidence_ref  text,
  started_at        timestamptz,
  finished_at       timestamptz,
  created_by        uuid NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN dq.import_batch.legal_basis_confirmed IS
  'An import cannot be committed unless an authorised user confirms the lawful basis for the data.';

CREATE TABLE dq.import_row_error (
  id                bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  batch_id          uuid NOT NULL REFERENCES dq.import_batch(id) ON DELETE CASCADE,
  row_number        integer NOT NULL,
  raw               jsonb NOT NULL,
  error_code        text NOT NULL,
  error_message     text NOT NULL
);
CREATE INDEX idx_importerr_batch ON dq.import_row_error(batch_id);

CREATE TABLE dq.merge_event (
  id                bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  entity_type       text NOT NULL,
  surviving_id      uuid NOT NULL,
  merged_id         uuid NOT NULL,
  merged_snapshot   jsonb NOT NULL,             -- full row, to allow un-merge
  reason            text,
  merged_by         uuid NOT NULL,
  merged_at         timestamptz NOT NULL DEFAULT now(),
  reverted_at       timestamptz,
  reverted_by       uuid
);
CREATE INDEX idx_merge_surviving ON dq.merge_event(entity_type, surviving_id);

CREATE TABLE dq.dedupe_candidate (
  id                bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  entity_type       text NOT NULL,
  id_a              uuid NOT NULL,
  id_b              uuid NOT NULL,
  score             numeric(4,3) NOT NULL CHECK (score BETWEEN 0 AND 1),
  signals           jsonb NOT NULL DEFAULT '{}'::jsonb,
  status            text NOT NULL DEFAULT 'open', -- open/merged/rejected
  reviewed_by       uuid,
  reviewed_at       timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_type, id_a, id_b),
  CHECK (id_a < id_b)
);
CREATE INDEX idx_dedupe_open ON dq.dedupe_candidate(entity_type, score DESC) WHERE status = 'open';

-- ---------------------------------------------------------------------
-- 12. Audit & compliance
-- ---------------------------------------------------------------------
CREATE TABLE audit.change_log (
  id                bigint NOT NULL GENERATED BY DEFAULT AS IDENTITY,
  table_name        text NOT NULL,
  record_id         text NOT NULL,
  operation         char(1) NOT NULL CHECK (operation IN ('I','U','D')),
  changed_fields    jsonb,
  old_values        jsonb,
  new_values        jsonb,
  actor_user_id     uuid,
  actor_ip          inet,
  request_id        text,
  changed_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, changed_at)
) PARTITION BY RANGE (changed_at);
CREATE TABLE audit.change_log_p_default PARTITION OF audit.change_log DEFAULT;
CREATE INDEX idx_changelog_record ON audit.change_log(table_name, record_id, changed_at DESC);

CREATE TABLE audit.data_access_log (
  id                bigint NOT NULL GENERATED BY DEFAULT AS IDENTITY,
  actor_user_id     uuid NOT NULL,
  action            text NOT NULL,              -- view_pii, export, bulk_read, search
  entity_type       text,
  record_count      integer,
  filter_json       jsonb,
  reason            text,                       -- mandatory for exports
  ip_address        inet,
  occurred_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);
CREATE TABLE audit.data_access_log_p_default PARTITION OF audit.data_access_log DEFAULT;
CREATE INDEX idx_accesslog_actor ON audit.data_access_log(actor_user_id, occurred_at DESC);

-- Data-subject requests (DPDP)
CREATE TABLE audit.dsr_request (
  id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  request_type      text NOT NULL,              -- access/correction/erasure/grievance/nomination
  subject_type      text NOT NULL,
  subject_id        uuid,
  requester_contact text NOT NULL,
  identity_verified boolean NOT NULL DEFAULT false,
  received_at       timestamptz NOT NULL DEFAULT now(),
  due_at            timestamptz NOT NULL,
  status            text NOT NULL DEFAULT 'received',
  resolution        text,
  resolved_at       timestamptz,
  handled_by        uuid,
  artefact_key      text                        -- S3 key of the response package
);
CREATE INDEX idx_dsr_open ON audit.dsr_request(due_at) WHERE status <> 'closed';

-- ---------------------------------------------------------------------
-- 13. Views
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW comm.v_messageable_farmer AS
SELECT f.id, f.state_id, f.first_name, f.last_name, f.district_id, f.village_id,
       cp.value_normalised AS phone, cc.channel, cc.purpose
FROM core.farmer f
JOIN core.person p       ON p.id = f.person_id
JOIN core.contact_point cp ON cp.person_id = p.id AND cp.kind IN ('mobile','whatsapp')
JOIN comm.consent_current cc
     ON cc.subject_type = 'farmer' AND cc.subject_id = f.id
WHERE cc.status = 'opted_in'
  AND (cc.expires_at IS NULL OR cc.expires_at > now())
  AND cp.verification <> 'do_not_contact'
  AND cp.delivery_failures < 3
  AND f.quality_tier <> 'quarantine'
  AND NOT f.is_deleted
  AND NOT EXISTS (
    SELECT 1 FROM comm.suppression s
    WHERE s.value_normalised = cp.value_normalised
      AND (s.channel IS NULL OR s.channel = cc.channel)
  );
COMMENT ON VIEW comm.v_messageable_farmer IS
  'The ONLY approved source of recipients for any outbound farmer campaign. Do not query core.farmer directly for sends.';

CREATE OR REPLACE VIEW crm.v_pipeline_summary AS
SELECT o.owner_user_id,
       o.stage,
       count(*)                       AS opp_count,
       sum(o.value_inr)               AS total_value,
       sum(o.weighted_value_inr)      AS weighted_value,
       avg(EXTRACT(day FROM now() - o.stage_entered_at))::numeric(8,1) AS avg_days_in_stage
FROM crm.opportunity o
WHERE NOT o.is_deleted AND o.stage NOT IN ('won','lost')
GROUP BY o.owner_user_id, o.stage;

CREATE OR REPLACE VIEW core.v_org_directory AS
SELECT o.id, o.org_code, o.type, o.status, o.name, o.name_local,
       s.name AS state_name, d.name AS district_name,
       o.member_count, o.quality_tier, o.completeness_score,
       (SELECT count(*) FROM core.person_org_role r
         WHERE r.organisation_id = o.id AND r.valid_to IS NULL) AS active_people,
       (SELECT cp.value_normalised FROM core.contact_point cp
         WHERE cp.organisation_id = o.id AND cp.is_primary LIMIT 1) AS primary_contact
FROM core.organisation o
LEFT JOIN ref.state s    ON s.id = o.state_id
LEFT JOIN ref.district d ON d.id = o.district_id
WHERE NOT o.is_deleted;

-- ---------------------------------------------------------------------
-- 14. Triggers
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.fn_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

CREATE TRIGGER trg_org_touch   BEFORE UPDATE ON core.organisation
  FOR EACH ROW EXECUTE FUNCTION core.fn_touch_updated_at();
CREATE TRIGGER trg_person_touch BEFORE UPDATE ON core.person
  FOR EACH ROW EXECUTE FUNCTION core.fn_touch_updated_at();
CREATE TRIGGER trg_farmer_touch BEFORE UPDATE ON core.farmer
  FOR EACH ROW EXECUTE FUNCTION core.fn_touch_updated_at();
CREATE TRIGGER trg_opp_touch    BEFORE UPDATE ON crm.opportunity
  FOR EACH ROW EXECUTE FUNCTION core.fn_touch_updated_at();

-- Derive farmer_class from total_area_ha (Government of India size classes)
CREATE OR REPLACE FUNCTION core.fn_derive_farmer_class() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.farmer_class := CASE
    WHEN NEW.total_area_ha IS NULL      THEN 'unknown'::core.farmer_class
    WHEN NEW.total_area_ha < 1          THEN 'marginal'
    WHEN NEW.total_area_ha < 2          THEN 'small'
    WHEN NEW.total_area_ha < 4          THEN 'semi_medium'
    WHEN NEW.total_area_ha < 10         THEN 'medium'
    ELSE 'large'
  END;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_farmer_class BEFORE INSERT OR UPDATE OF total_area_ha ON core.farmer
  FOR EACH ROW EXECUTE FUNCTION core.fn_derive_farmer_class();

-- Maintain comm.consent_current from the append-only ledger
CREATE OR REPLACE FUNCTION comm.fn_sync_consent_current() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO comm.consent_current AS c
    (subject_type, subject_id, channel, purpose, status, effective_at, expires_at, last_event_id)
  VALUES
    (NEW.subject_type, NEW.subject_id, NEW.channel, NEW.purpose, NEW.status,
     NEW.captured_at, NEW.expires_at, NEW.id)
  ON CONFLICT (subject_type, subject_id, channel, purpose) DO UPDATE
    SET status        = EXCLUDED.status,
        effective_at  = EXCLUDED.effective_at,
        expires_at    = EXCLUDED.expires_at,
        last_event_id = EXCLUDED.last_event_id
    WHERE EXCLUDED.effective_at >= c.effective_at;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_consent_sync AFTER INSERT ON comm.consent_event
  FOR EACH ROW EXECUTE FUNCTION comm.fn_sync_consent_current();

-- Block UPDATE/DELETE on the consent ledger
CREATE OR REPLACE FUNCTION comm.fn_consent_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'comm.consent_event is append-only; insert a new event instead';
END $$;

CREATE TRIGGER trg_consent_no_update BEFORE UPDATE OR DELETE ON comm.consent_event
  FOR EACH ROW EXECUTE FUNCTION comm.fn_consent_append_only();

-- Record opportunity stage transitions
CREATE OR REPLACE FUNCTION crm.fn_track_opp_stage() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.stage IS DISTINCT FROM OLD.stage THEN
    INSERT INTO crm.opportunity_stage_history
      (opportunity_id, from_stage, to_stage, changed_at, days_in_from_stage)
    VALUES
      (NEW.id, OLD.stage, NEW.stage, now(),
       GREATEST(0, (EXTRACT(epoch FROM now() - OLD.stage_entered_at) / 86400)::integer));
    NEW.stage_entered_at := now();
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_opp_stage BEFORE UPDATE ON crm.opportunity
  FOR EACH ROW EXECUTE FUNCTION crm.fn_track_opp_stage();

-- Roll invoice line amounts up to the header.
--
-- 🔴 Round per line, then sum. Summing unrounded values and rounding the total
-- produces a total that disagrees with the printed line table by a rupee, and
-- a customer's accounts team will reject the document over it.
CREATE OR REPLACE FUNCTION crm.fn_invoice_rollup() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  target uuid := COALESCE(NEW.invoice_id, OLD.invoice_id);
BEGIN
  UPDATE crm.invoice i SET
    taxable_value = COALESCE(t.taxable, 0),
    tax_amount    = COALESCE(t.tax, 0),
    total_value   = COALESCE(t.total, 0),
    updated_at    = now()
  FROM (
    SELECT sum(line_taxable_value) AS taxable,
           sum(line_tax_amount)    AS tax,
           sum(line_total)         AS total
    FROM crm.invoice_line WHERE invoice_id = target
  ) t
  WHERE i.id = target;
  RETURN NULL;
END $$;

CREATE TRIGGER trg_invline_rollup
  AFTER INSERT OR UPDATE OR DELETE ON crm.invoice_line
  FOR EACH ROW EXECUTE FUNCTION crm.fn_invoice_rollup();

-- Move an issued invoice between part_paid and paid as receipts land.
--
-- Deliberately does not touch cancelled, on_hold or draft: a payment against a
-- cancelled invoice is a problem for a human to look at, not something to
-- resolve by silently marking the document paid.
CREATE OR REPLACE FUNCTION crm.fn_invoice_payment_status() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  target uuid := COALESCE(NEW.invoice_id, OLD.invoice_id);
  received numeric(14,2);
  inv crm.invoice%ROWTYPE;
BEGIN
  SELECT * INTO inv FROM crm.invoice WHERE id = target;
  IF inv.status NOT IN ('issued','part_paid','paid') THEN
    RETURN NULL;
  END IF;

  SELECT COALESCE(sum(amount), 0) INTO received
  FROM crm.invoice_payment WHERE invoice_id = target;

  UPDATE crm.invoice SET
    status = CASE
               WHEN received <= 0            THEN 'issued'::crm.invoice_status
               WHEN received >= total_value  THEN 'paid'::crm.invoice_status
               ELSE 'part_paid'::crm.invoice_status
             END,
    updated_at = now()
  WHERE id = target;
  RETURN NULL;
END $$;

CREATE TRIGGER trg_invpay_status
  AFTER INSERT OR UPDATE OR DELETE ON crm.invoice_payment
  FOR EACH ROW EXECUTE FUNCTION crm.fn_invoice_payment_status();

-- 🔴 An issued invoice number is permanent.
--
-- This is the whole point of the billing register. The FY26 data cancelled
-- TEPL/2026-27/03 and then reissued a different document under the same
-- number; from here that raises instead. Cancelling keeps the number, and the
-- series has already moved past it.
CREATE OR REPLACE FUNCTION crm.fn_invoice_no_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.invoice_no IS NOT NULL AND NEW.invoice_no IS DISTINCT FROM OLD.invoice_no THEN
    RAISE EXCEPTION
      'invoice_no % is already allocated and cannot be changed or reused (invoice %)',
      OLD.invoice_no, OLD.id;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_invoice_no_immutable BEFORE UPDATE ON crm.invoice
  FOR EACH ROW EXECUTE FUNCTION crm.fn_invoice_no_immutable();

-- =====================================================================
--  End of schema
-- =====================================================================
