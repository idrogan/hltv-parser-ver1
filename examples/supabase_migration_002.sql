-- Migration 002 — run ONCE in Supabase → SQL Editor, after supabase_schema_finance.sql.
-- Idempotent: safe to re-run.
--
-- 1. Allow more registries (ro_anaf now; fi_prh / fr_inpi / se_bolagsverket / be_nbb reserved).
-- 2. financial_fact.segment: '' instead of NULL, so the unique key works for upserts
--    (Postgres treats NULLs as distinct, which made on_conflict silently insert duplicates).
-- 3. Seed PGL (Romania, ANAF CUI 16896480) as a monitored entity.

-- 1 ------------------------------------------------------------------------
alter table legal_entity drop constraint if exists legal_entity_registry_check;
alter table legal_entity add constraint legal_entity_registry_check
  check (registry in ('gb_ch', 'dk_cvr', 'no_brreg', 'us_sec', 'ro_anaf',
                      'fi_prh', 'fr_inpi', 'se_bolagsverket', 'be_nbb',
                      'de_ur', 'fr_rcs', 'nl_kvk', 'other'));

-- 2 ------------------------------------------------------------------------
update financial_fact set segment = '' where segment is null;
alter table financial_fact alter column segment set default '';
alter table financial_fact alter column segment set not null;

-- 3 ------------------------------------------------------------------------
insert into org_brand (slug, name, kind, notes) values
  ('pgl', 'PGL', 'tournament_operator', 'CS Major operator (Stockholm 2021, Antwerp 2022, Copenhagen 2024, ...)')
on conflict (slug) do nothing;

insert into legal_entity (registry, entity_id, name, country, notes) values
  ('ro_anaf', '16896480', 'PGL ESPORT SRL', 'RO', 'J2004017621401; ANAF bilant API returns 2019+')
on conflict (registry, entity_id) do nothing;

insert into brand_entity (brand_id, legal_entity_id, relation, source)
select b.id, e.id, 'operating', 'registry'
from org_brand b, legal_entity e
where b.slug = 'pgl' and e.registry = 'ro_anaf' and e.entity_id = '16896480'
on conflict do nothing;
