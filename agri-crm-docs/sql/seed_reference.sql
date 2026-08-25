-- =====================================================================
--  AgriCRM — reference seed data + partition generator
--  Run AFTER schema.sql
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. States & UTs (LGD codes)
-- ---------------------------------------------------------------------
INSERT INTO ref.state (id, lgd_code, name, is_ut) VALUES
  (28,28,'Andhra Pradesh',false),
  (12,12,'Arunachal Pradesh',false),
  (18,18,'Assam',false),
  (10,10,'Bihar',false),
  (22,22,'Chhattisgarh',false),
  (30,30,'Goa',false),
  (24,24,'Gujarat',false),
  (6,6,'Haryana',false),
  (2,2,'Himachal Pradesh',false),
  (20,20,'Jharkhand',false),
  (29,29,'Karnataka',false),
  (32,32,'Kerala',false),
  (23,23,'Madhya Pradesh',false),
  (27,27,'Maharashtra',false),
  (14,14,'Manipur',false),
  (17,17,'Meghalaya',false),
  (15,15,'Mizoram',false),
  (13,13,'Nagaland',false),
  (21,21,'Odisha',false),
  (3,3,'Punjab',false),
  (8,8,'Rajasthan',false),
  (11,11,'Sikkim',false),
  (33,33,'Tamil Nadu',false),
  (36,36,'Telangana',false),
  (16,16,'Tripura',false),
  (9,9,'Uttar Pradesh',false),
  (5,5,'Uttarakhand',false),
  (19,19,'West Bengal',false),
  (35,35,'Andaman and Nicobar Islands',true),
  (4,4,'Chandigarh',true),
  (26,26,'Dadra and Nagar Haveli and Daman and Diu',true),
  (7,7,'Delhi',true),
  (1,1,'Jammu and Kashmir',true),
  (37,37,'Ladakh',true),
  (31,31,'Lakshadweep',true),
  (34,34,'Puducherry',true)
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------
-- 2. Farmer table partitions — one per state
--    Big cane/agri states get their own; the rest fall to default.
--    Add all 36 if you expect nationwide volume.
-- ---------------------------------------------------------------------
DO $$
DECLARE s record;
BEGIN
  FOR s IN SELECT id, name FROM ref.state ORDER BY id LOOP
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS core.farmer_p_%s PARTITION OF core.farmer FOR VALUES IN (%s)',
      s.id, s.id);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- 3. Monthly partitions for high-volume tables (next 24 months)
-- ---------------------------------------------------------------------
DO $$
DECLARE
  d       date := date_trunc('month', current_date)::date;
  i       int;
  tbl     text;
  sch     text;
  base    text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['comm.message','crm.activity','audit.change_log','audit.data_access_log'] LOOP
    sch  := split_part(tbl, '.', 1);
    base := split_part(tbl, '.', 2);
    FOR i IN 0..23 LOOP
      EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
        sch,
        base || '_p_' || to_char(d + (i || ' month')::interval, 'YYYYMM'),
        sch, base,
        (d + (i || ' month')::interval)::date,
        (d + ((i+1) || ' month')::interval)::date);
    END LOOP;
  END LOOP;
END $$;
-- NOTE: run this monthly from a cron job, or use pg_partman.

-- ---------------------------------------------------------------------
-- 4. Approved data sources — the compliance whitelist
--    🔴 A collector MUST NOT run against a source that is not is_approved.
-- ---------------------------------------------------------------------
INSERT INTO dq.source (code, name, kind, url, legal_basis, contains_pii, is_approved, refresh_cadence) VALUES
 ('mca_master_data','MCA21 Company / Director Master Data','public_registry',
  'https://www.mca.gov.in',
  'Statutorily published corporate records. Director names and DINs are published by law under the Companies Act 2013 and are business identifiers, not private personal data.',
  false,true,'quarterly'),

 ('sfac_fpo_list','SFAC state-wise FPO lists','public_registry',
  'https://sfacindia.com',
  'Government-published institutional directory of registered FPOs. Organisational data only.',
  false,true,'quarterly'),

 ('ogd_data_gov_in','Open Government Data Platform (data.gov.in)','open_government_data',
  'https://www.data.gov.in',
  'Published under the National Data Sharing and Accessibility Policy with an open Government Open Data Licence - India.',
  false,true,'weekly'),

 ('agmarknet','AGMARKNET mandi arrivals and prices','open_government_data',
  'https://agmarknet.gov.in',
  'Open government data. Aggregate market data, no personal data.',
  false,true,'daily'),

 ('isma_directory','Indian Sugar Mills Association member directory','industry_directory',
  'https://www.indiansugar.com',
  'Publicly published trade-association directory of member mills. Institutional contact data.',
  false,true,'quarterly'),

 ('nfcsf_directory','National Federation of Cooperative Sugar Factories directory','industry_directory',
  'https://coopsugar.org',
  'Publicly published federation directory of cooperative sugar factories. Institutional data.',
  false,true,'quarterly'),

 ('state_sugarfed','State Sugarfed / Cane Commissioner mill lists','public_registry',
  NULL,
  'State government published lists of licensed sugar mills and their command areas.',
  false,true,'annual'),

 ('lgd_directory','Local Government Directory (state/district/block/village codes)','public_registry',
  'https://lgdirectory.gov.in',
  'Government reference data. No personal data.',
  false,true,'quarterly'),

 ('org_website','Organisation official website contact page','official_website',
  NULL,
  'Business contact details that the organisation itself published for the purpose of being contacted. Institutional, not personal.',
  false,true,'annual'),

 ('partner_mou','Data shared under a signed FPO / mill / cooperative MoU','partner_agreement',
  NULL,
  'Contractual data share with a consent clause executed by the member. Consent artefact must be filed against every batch.',
  true,true,'on_event'),

 ('field_collection','Field agent collection with in-app consent capture','field_collection',
  NULL,
  'Consent obtained directly from the data principal at the point of collection, with a notice shown in their language (DPDP s.5-6).',
  true,true,'continuous'),

 ('inbound_signup','Farmer self-registration (web / WhatsApp / IVR)','inbound_signup',
  NULL,
  'Consent given by the data principal at the point of signup.',
  true,true,'continuous'),

 ('theta_analytics','Theta Analytics legacy farmer database','theta_analytics',
  NULL,
  'PENDING REVIEW - lawful basis must be documented per source batch before this is marked approved.',
  true,false,'one_off')
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 5. Core crops
-- ---------------------------------------------------------------------
INSERT INTO ref.crop (code, name, category, default_season) VALUES
 ('SUGARCANE','Sugarcane','cash','perennial'),
 ('WHEAT','Wheat','cereal','rabi'),
 ('PADDY','Paddy / Rice','cereal','kharif'),
 ('MAIZE','Maize','cereal','kharif'),
 ('COTTON','Cotton','cash','kharif'),
 ('SOYBEAN','Soybean','oilseed','kharif'),
 ('MUSTARD','Mustard','oilseed','rabi'),
 ('GROUNDNUT','Groundnut','oilseed','kharif'),
 ('BAJRA','Pearl Millet (Bajra)','cereal','kharif'),
 ('JOWAR','Sorghum (Jowar)','cereal','kharif'),
 ('GRAM','Chickpea (Gram)','pulse','rabi'),
 ('TUR','Pigeon Pea (Tur/Arhar)','pulse','kharif'),
 ('MUSTARD_TORIA','Toria','oilseed','rabi'),
 ('POTATO','Potato','horticulture','rabi'),
 ('ONION','Onion','horticulture','rabi'),
 ('TOMATO','Tomato','horticulture','annual'),
 ('BANANA','Banana','horticulture','perennial'),
 ('TURMERIC','Turmeric','horticulture','kharif'),
 ('BERSEEM','Berseem','fodder','rabi')
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 6. Sanity checks
-- ---------------------------------------------------------------------
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM ref.state;
  RAISE NOTICE 'states seeded: %', n;
  SELECT count(*) INTO n FROM dq.source WHERE is_approved;
  RAISE NOTICE 'approved sources: %', n;
  SELECT count(*) INTO n FROM pg_tables
    WHERE schemaname = 'core' AND tablename LIKE 'farmer\_p\_%';
  RAISE NOTICE 'farmer partitions: %', n;
  SELECT count(*) INTO n FROM pg_tables
    WHERE tablename ~ '_p_[0-9]{6}$';
  RAISE NOTICE 'monthly partitions: %', n;
END $$;
