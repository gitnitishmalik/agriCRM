-- Runs once, on first container start, before schema.sql.
-- schema.sql creates uuid-ossp / pg_trgm / btree_gist / unaccent itself;
-- PostGIS is created here because schema.sql leaves it commented out
-- (Doc 02 — geometry columns are enabled when the instance supports it).
CREATE EXTENSION IF NOT EXISTS postgis;
