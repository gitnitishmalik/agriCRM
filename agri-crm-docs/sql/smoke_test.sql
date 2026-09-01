-- =====================================================================
--  AgriCRM — smoke test
--  Proves the schema, constraints and triggers actually behave.
--  Run after schema.sql + seed_reference.sql. Rolls itself back at the end
--  unless you comment out the final ROLLBACK.
-- =====================================================================
BEGIN;

\set ON_ERROR_STOP on

-- --- reference rows -------------------------------------------------
INSERT INTO ref.district (id, state_id, name) VALUES (9001, 9, 'Muzaffarnagar');
INSERT INTO ref.block    (id, district_id, name) VALUES (90011, 9001, 'Khatauli');
INSERT INTO ref.village  (id, block_id, district_id, name, pincode)
  VALUES (900111, 90011, 9001, 'Bhainswal', '251201');

-- --- an FPO ---------------------------------------------------------
INSERT INTO core.organisation
  (id, org_code, type, status, legal_form, name, cin, state_id, district_id, block_id, village_id,
   member_count, women_member_count, established_year,
   primary_source_id)
VALUES
  ('11111111-1111-1111-1111-111111111111','FPO-UP-000001','fpo','active','producer_company',
   'Bhainswal Kisan Producer Company Limited','U01100UP2021PTC123456',
   9, 9001, 90011, 900111, 1250, 310, 2021,
   (SELECT id FROM dq.source WHERE code='sfac_fpo_list'));

INSERT INTO core.fpo_profile (organisation_id, paid_up_capital, shareholder_count,
                              business_lines, implementing_agency, primary_crops)
VALUES ('11111111-1111-1111-1111-111111111111', 1250000, 1250,
        ARRAY['input_sale','output_aggregation','custom_hiring'],'NABARD',
        ARRAY[(SELECT id FROM ref.crop WHERE code='SUGARCANE')]);

-- --- a sugar mill ---------------------------------------------------
INSERT INTO core.organisation
  (id, org_code, type, status, legal_form, name, state_id, district_id,
   primary_source_id)
VALUES
  ('22222222-2222-2222-2222-222222222222','MILL-UP-000001','sugar_mill','active','public_limited',
   'Khatauli Sugar Mill', 9, 9001,
   (SELECT id FROM dq.source WHERE code='isma_directory'));

INSERT INTO core.sugar_mill_profile
  (organisation_id, ownership, crushing_capacity_tcd, avg_recovery_pct,
   registered_cane_growers, season_start_month, season_end_month, federation_membership)
VALUES ('22222222-2222-2222-2222-222222222222','private', 16000, 11.25, 92000, 11, 4,
        ARRAY['ISMA']);

-- --- a person holding a role in the FPO ------------------------------
INSERT INTO core.person (id, first_name, last_name, father_or_spouse, gender, din, district_id)
VALUES ('33333333-3333-3333-3333-333333333333','Ramesh','Chaudhary','Sohan Singh','male','01234567',9001);

INSERT INTO core.person_org_role
  (person_id, organisation_id, role, designation_text, is_primary_contact, is_decision_maker, valid_from)
VALUES ('33333333-3333-3333-3333-333333333333','11111111-1111-1111-1111-111111111111',
        'managing_director','MD & CEO', true, true, DATE '2021-06-01');

INSERT INTO core.contact_point
  (person_id, kind, value_raw, value_normalised, is_primary, verification, is_whatsapp_capable, source_id)
VALUES ('33333333-3333-3333-3333-333333333333','mobile','98765 43210','+919876543210',
        true,'verified',true,(SELECT id FROM dq.source WHERE code='field_collection'));

-- --- a farmer -------------------------------------------------------
INSERT INTO core.farmer
  (id, state_id, farmer_code, person_id, first_name, last_name, father_or_spouse, gender,
   district_id, block_id, village_id, total_area_ha, area_source, irrigated_area_ha,
   primary_crop_id, primary_fpo_id, supplying_mill_id, mill_supplier_code,
   primary_source_id)
VALUES
  ('44444444-4444-4444-4444-444444444444', 9, 'FRM-UP-00000001',
   '33333333-3333-3333-3333-333333333333','Ramesh','Chaudhary','Sohan Singh','male',
   9001, 90011, 900111, 3.2400, 'document', 3.2400,
   (SELECT id FROM ref.crop WHERE code='SUGARCANE'),
   '11111111-1111-1111-1111-111111111111',
   '22222222-2222-2222-2222-222222222222','KTL-88231',
   (SELECT id FROM dq.source WHERE code='field_collection'));

-- TEST 1: farmer_class must be derived, not entered. 3.24 ha -> semi_medium
DO $$
DECLARE c core.farmer_class;
BEGIN
  SELECT farmer_class INTO c FROM core.farmer WHERE id='44444444-4444-4444-4444-444444444444';
  ASSERT c = 'semi_medium', format('TEST 1 FAILED: expected semi_medium, got %s', c);
  RAISE NOTICE 'TEST 1 PASS  farmer_class derived = %', c;
END $$;

-- TEST 2: partition routing — the row must live in core.farmer_p_9
DO $$
DECLARE t text;
BEGIN
  SELECT tableoid::regclass::text INTO t FROM core.farmer
   WHERE id='44444444-4444-4444-4444-444444444444';
  ASSERT t = 'core.farmer_p_9', format('TEST 2 FAILED: row landed in %s', t);
  RAISE NOTICE 'TEST 2 PASS  partition routing = %', t;
END $$;

-- TEST 3: irrigated area may not exceed total area
DO $$
BEGIN
  BEGIN
    UPDATE core.farmer SET irrigated_area_ha = 99
     WHERE id='44444444-4444-4444-4444-444444444444';
    RAISE EXCEPTION 'TEST 3 FAILED: constraint did not fire';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'TEST 3 PASS  irrigated<=total enforced';
  END;
END $$;

-- --- land + crop ----------------------------------------------------
INSERT INTO core.land_parcel (farmer_id, farmer_state_id, khasra_number, area_ha, tenure, irrigation,
                              village_id, area_verified, verification_method)
VALUES ('44444444-4444-4444-4444-444444444444', 9, '221/3', 3.2400, 'owned','tubewell',
        900111, true, 'land_record');

INSERT INTO core.farmer_crop (farmer_id, farmer_state_id, crop_id, season, crop_year, area_ha,
                              expected_yield_mt, sold_to_org_id)
VALUES ('44444444-4444-4444-4444-444444444444', 9,
        (SELECT id FROM ref.crop WHERE code='SUGARCANE'),'perennial',2026, 3.2400, 259.2,
        '22222222-2222-2222-2222-222222222222');

INSERT INTO core.farmer_org_link (farmer_id, farmer_state_id, organisation_id, relationship,
                                  member_code, shares_held, joined_on)
VALUES ('44444444-4444-4444-4444-444444444444', 9,'11111111-1111-1111-1111-111111111111',
        'fpo_member','BKP-0042', 10, DATE '2021-08-15');

-- --- consent --------------------------------------------------------
INSERT INTO comm.consent_event
  (subject_type, subject_id, channel, purpose, status, evidence_type, evidence_ref,
   language, notice_version)
VALUES ('farmer','44444444-4444-4444-4444-444444444444','whatsapp','advisory','opted_in',
        'signed_form','CONSENT-2026-000871','hi','notice-v1.2');

-- TEST 4: consent_current must be maintained by trigger
DO $$
DECLARE st comm.consent_status;
BEGIN
  SELECT status INTO st FROM comm.consent_current
   WHERE subject_id='44444444-4444-4444-4444-444444444444' AND channel='whatsapp' AND purpose='advisory';
  ASSERT st = 'opted_in', format('TEST 4 FAILED: got %s', st);
  RAISE NOTICE 'TEST 4 PASS  consent_current synced = %', st;
END $$;

-- TEST 5: consent ledger is append-only
DO $$
BEGIN
  BEGIN
    UPDATE comm.consent_event SET status='opted_out'
     WHERE subject_id='44444444-4444-4444-4444-444444444444';
    RAISE EXCEPTION 'TEST 5 FAILED: update was allowed';
  EXCEPTION WHEN raise_exception THEN
    IF sqlerrm LIKE '%append-only%' THEN
      RAISE NOTICE 'TEST 5 PASS  consent ledger is append-only';
    ELSE RAISE;
    END IF;
  END;
END $$;

-- TEST 6: the messageable view returns the farmer
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM comm.v_messageable_farmer
   WHERE id='44444444-4444-4444-4444-444444444444';
  ASSERT n = 1, format('TEST 6 FAILED: expected 1 messageable row, got %s', n);
  RAISE NOTICE 'TEST 6 PASS  farmer is messageable';
END $$;

-- TEST 7: opting out removes them within the same transaction
INSERT INTO comm.consent_event
  (subject_type, subject_id, channel, purpose, status, evidence_type, evidence_ref,
   language, notice_version)
VALUES ('farmer','44444444-4444-4444-4444-444444444444','whatsapp','advisory','opted_out',
        'sms_keyword','wamid.HBg...STOP','hi','notice-v1.2');

DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM comm.v_messageable_farmer
   WHERE id='44444444-4444-4444-4444-444444444444';
  ASSERT n = 0, format('TEST 7 FAILED: opted-out farmer still messageable (%s rows)', n);
  RAISE NOTICE 'TEST 7 PASS  opt-out removes farmer from messageable audience';
END $$;

-- TEST 8: suppression list overrides everything
INSERT INTO comm.consent_event
  (subject_type, subject_id, channel, purpose, status, evidence_type, language, notice_version)
VALUES ('farmer','44444444-4444-4444-4444-444444444444','whatsapp','advisory','opted_in',
        'in_app_checkbox','hi','notice-v1.2');
INSERT INTO comm.suppression (value_normalised, channel, reason)
VALUES ('+919876543210','whatsapp','complaint');

DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM comm.v_messageable_farmer
   WHERE id='44444444-4444-4444-4444-444444444444';
  ASSERT n = 0, format('TEST 8 FAILED: suppressed number still messageable (%s rows)', n);
  RAISE NOTICE 'TEST 8 PASS  suppression overrides a fresh opt-in';
END $$;

-- --- pipeline -------------------------------------------------------
INSERT INTO crm.opportunity
  (id, opp_code, name, organisation_id, primary_contact_id, type, stage, probability_pct,
   value_inr, expected_close_date, owner_user_id)
VALUES ('55555555-5555-5555-5555-555555555555','OPP-2026-0001',
        'Cane yield analytics pilot - Khatauli','22222222-2222-2222-2222-222222222222',
        '33333333-3333-3333-3333-333333333333','cane_yield_analytics','qualified', 40,
        4500000, DATE '2026-11-30','66666666-6666-6666-6666-666666666666');

-- TEST 9: weighted value is a generated column
DO $$
DECLARE w numeric;
BEGIN
  SELECT weighted_value_inr INTO w FROM crm.opportunity WHERE id='55555555-5555-5555-5555-555555555555';
  ASSERT w = 1800000, format('TEST 9 FAILED: expected 1800000, got %s', w);
  RAISE NOTICE 'TEST 9 PASS  weighted_value_inr = %', w;
END $$;

-- TEST 10: stage transition is recorded automatically
UPDATE crm.opportunity SET stage='proposal_sent', probability_pct=60
 WHERE id='55555555-5555-5555-5555-555555555555';

DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM crm.opportunity_stage_history
   WHERE opportunity_id='55555555-5555-5555-5555-555555555555'
     AND from_stage='qualified' AND to_stage='proposal_sent';
  ASSERT n = 1, format('TEST 10 FAILED: %s history rows', n);
  RAISE NOTICE 'TEST 10 PASS  stage history written automatically';
END $$;

-- TEST 11: a lost opportunity must carry a loss reason
DO $$
BEGIN
  BEGIN
    UPDATE crm.opportunity SET stage='lost' WHERE id='55555555-5555-5555-5555-555555555555';
    RAISE EXCEPTION 'TEST 11 FAILED: lost without reason was allowed';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'TEST 11 PASS  loss_reason required when stage=lost';
  END;
END $$;

-- TEST 12: only one primary contact per organisation
DO $$
BEGIN
  BEGIN
    INSERT INTO core.person_org_role (person_id, organisation_id, role, is_primary_contact)
    VALUES ('33333333-3333-3333-3333-333333333333','11111111-1111-1111-1111-111111111111',
            'director', true);
    RAISE EXCEPTION 'TEST 12 FAILED: second primary contact was allowed';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'TEST 12 PASS  single primary contact enforced per org';
  END;
END $$;

-- TEST 13: contact_point must belong to exactly one owner
DO $$
BEGIN
  BEGIN
    INSERT INTO core.contact_point (person_id, organisation_id, kind, value_raw, value_normalised)
    VALUES ('33333333-3333-3333-3333-333333333333','11111111-1111-1111-1111-111111111111',
            'mobile','1','+911');
    RAISE EXCEPTION 'TEST 13 FAILED: dual-owner contact point was allowed';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'TEST 13 PASS  contact_point has exactly one owner';
  END;
END $$;

-- TEST 14: fuzzy org search finds a misspelled name
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM core.organisation
   WHERE similarity(name, 'Bhainsval Kisan Producer Co') > 0.30;
  ASSERT n >= 1, 'TEST 14 FAILED: trigram search found nothing';
  RAISE NOTICE 'TEST 14 PASS  trigram fuzzy match works';
END $$;

-- TEST 15: directory view joins cleanly
DO $$
DECLARE r record;
BEGIN
  SELECT * INTO r FROM core.v_org_directory WHERE id='11111111-1111-1111-1111-111111111111';
  ASSERT r.state_name = 'Uttar Pradesh', 'TEST 15 FAILED: state join';
  ASSERT r.active_people = 1, format('TEST 15 FAILED: active_people=%s', r.active_people);
  RAISE NOTICE 'TEST 15 PASS  org directory view: % / % / % active people',
               r.name, r.state_name, r.active_people;
END $$;

-- =====================================================================
--  Billing  (INVOICE.md)
-- =====================================================================

-- TEST 16: area converts to hectares on the way in
--
-- 🔴 CLAUDE.md requires every area in hectares. Acres and square kilometres
-- are input conveniences and the generated column is where they stop being
-- ambiguous. 2301 acres and 65.7 sq km are both real quantities off real
-- invoices.
DO $$
DECLARE ent uuid; inv uuid; acre_ha numeric; sqkm_ha numeric;
BEGIN
  INSERT INTO crm.billing_entity (code, legal_name, state_code, gstin, valid_from)
  VALUES ('TEST', 'Smoke Test Entity', '07', '07AAHCT0066D1ZM', '2024-04-01')
  RETURNING id INTO ent;

  INSERT INTO crm.invoice (billing_entity_id, entity_code, invoice_date, buyer_name)
  VALUES (ent, 'TEST', '2026-06-16', 'Syngenta India Private Limited')
  RETURNING id INTO inv;

  INSERT INTO crm.invoice_line
    (invoice_id, line_no, description, hsn_sac, quantity, unit, rate,
     line_taxable_value, line_tax_amount, line_total)
  VALUES
    (inv, 1, 'Drone Spraying Services', '998611', 2301, 'acre', 150,
     345150.00, 62127.00, 407277.00),
    (inv, 2, 'Base Map Generation',     '997319', 65.7, 'sq_km', 32000,
     2102400.00, 0, 2102400.00);

  SELECT quantity_ha INTO acre_ha FROM crm.invoice_line WHERE invoice_id=inv AND line_no=1;
  SELECT quantity_ha INTO sqkm_ha FROM crm.invoice_line WHERE invoice_id=inv AND line_no=2;

  -- 2301 x 0.40468564224 = 931.18166..., stored to 4 dp as 931.1817
  ASSERT acre_ha = 931.1817,
    format('TEST 16 FAILED: 2301 acres -> %s ha, expected 931.1817', acre_ha);
  ASSERT sqkm_ha = 6570,
    format('TEST 16 FAILED: 65.7 sq km -> %s ha, expected 6570', sqkm_ha);
  RAISE NOTICE 'TEST 16 PASS  area to hectares: 2301 ac = % ha, 65.7 km2 = % ha',
               acre_ha, sqkm_ha;
END $$;

-- TEST 17: line amounts roll up to the header by trigger
DO $$
DECLARE r record;
BEGIN
  SELECT i.* INTO r FROM crm.invoice i
   WHERE i.entity_code='TEST' ORDER BY i.created_at DESC LIMIT 1;
  ASSERT r.taxable_value = 2447550.00,
    format('TEST 17 FAILED: taxable=%s expected 2447550.00', r.taxable_value);
  ASSERT r.tax_amount = 62127.00,
    format('TEST 17 FAILED: tax=%s expected 62127.00', r.tax_amount);
  ASSERT r.total_value = 2509677.00,
    format('TEST 17 FAILED: total=%s expected 2509677.00', r.total_value);
  RAISE NOTICE 'TEST 17 PASS  header rollup: taxable %, tax %, total %',
               r.taxable_value, r.tax_amount, r.total_value;
END $$;

-- TEST 18: 🔴 an allocated invoice number can never be changed
--
-- This is the constraint that makes the FY26 defect impossible. That data
-- cancelled TEPL/2026-27/03 and reissued a different document under the same
-- number; the trigger now refuses.
DO $$
DECLARE inv uuid; ok boolean := false;
BEGIN
  SELECT id INTO inv FROM crm.invoice WHERE entity_code='TEST' LIMIT 1;
  UPDATE crm.invoice
     SET invoice_no='TEST/2026-27/1', financial_year='2026-27',
         status='issued', issued_at=now()
   WHERE id=inv;

  BEGIN
    UPDATE crm.invoice SET invoice_no='TEST/2026-27/2' WHERE id=inv;
  EXCEPTION WHEN raise_exception THEN
    ok := true;
  END;

  ASSERT ok, 'TEST 18 FAILED: an issued invoice number was allowed to change';
  RAISE NOTICE 'TEST 18 PASS  allocated invoice number is immutable';
END $$;

-- TEST 19: the same number cannot be used twice in one entity
DO $$
DECLARE ent uuid; ok boolean := false;
BEGIN
  SELECT billing_entity_id INTO ent FROM crm.invoice WHERE entity_code='TEST' LIMIT 1;
  BEGIN
    INSERT INTO crm.invoice
      (billing_entity_id, entity_code, invoice_no, financial_year,
       invoice_date, buyer_name, status, issued_at)
    VALUES (ent, 'TEST', 'TEST/2026-27/1', '2026-27',
            '2026-07-14', 'Syngenta India Private Limited', 'issued', now());
  EXCEPTION WHEN unique_violation THEN
    ok := true;
  END;
  ASSERT ok, 'TEST 19 FAILED: a duplicate invoice number was accepted';
  RAISE NOTICE 'TEST 19 PASS  duplicate invoice number rejected';
END $$;

-- TEST 20: payments drive status, and a cancellation reason is mandatory
DO $$
DECLARE inv uuid; st crm.invoice_status; ok boolean := false;
BEGIN
  SELECT id INTO inv FROM crm.invoice WHERE invoice_no='TEST/2026-27/1';

  INSERT INTO crm.invoice_payment (invoice_id, received_on, amount, mode)
  VALUES (inv, '2026-08-01', 1000000.00, 'rtgs');
  SELECT status INTO st FROM crm.invoice WHERE id=inv;
  ASSERT st = 'part_paid', format('TEST 20 FAILED: part payment gave %s', st);

  INSERT INTO crm.invoice_payment (invoice_id, received_on, amount, mode)
  VALUES (inv, '2026-08-20', 1509677.00, 'rtgs');
  SELECT status INTO st FROM crm.invoice WHERE id=inv;
  ASSERT st = 'paid', format('TEST 20 FAILED: full payment gave %s', st);

  BEGIN
    UPDATE crm.invoice SET status='cancelled', cancelled_at=now() WHERE id=inv;
  EXCEPTION WHEN check_violation THEN
    ok := true;
  END;
  ASSERT ok, 'TEST 20 FAILED: cancelled without a reason';
  RAISE NOTICE 'TEST 20 PASS  payments drive status; cancellation needs a reason';
END $$;

ROLLBACK;   -- comment out to keep the sample data
