-- Finance layer: who owns which brand, which legal entities file accounts,
-- and every filing we have seen in an official registry.
--
-- Run ONCE in Supabase → SQL Editor, after supabase_schema.sql.
-- Safe to re-run: every statement is idempotent.

-- ---------------------------------------------------------------------------
-- Brands (what fans call them) and the legal entities behind them
-- ---------------------------------------------------------------------------

create table if not exists org_brand (
  id          serial primary key,
  slug        text unique not null,              -- 'blast', 'fnatic'
  name        text not null,
  kind        text not null default 'team'
              check (kind in ('team', 'tournament_operator', 'holding', 'other')),
  notes       text,
  created_at  timestamptz default now()
);

create table if not exists legal_entity (
  id          serial primary key,
  registry    text not null
              check (registry in ('gb_ch', 'dk_cvr', 'no_brreg', 'us_sec',
                                  'de_ur', 'fr_rcs', 'nl_kvk', 'se_bolagsverket', 'other')),
  entity_id   text not null,                      -- company number / CVR / orgnr / CIK
  name        text not null,
  country     text,                               -- ISO-2
  monitor     boolean not null default true,      -- picked up by the daily registry workflow
  notes       text,
  created_at  timestamptz default now(),
  unique (registry, entity_id)
);

create table if not exists brand_entity (
  brand_id         int not null references org_brand on delete cascade,
  legal_entity_id  int not null references legal_entity on delete cascade,
  relation         text not null default 'operating'
                   check (relation in ('operating', 'holding', 'subsidiary', 'parent', 'owner', 'former')),
  share_pct        numeric,                       -- ownership %, when known
  source           text,                          -- where the link comes from (registry page, ORBIS check, press)
  primary key (brand_id, legal_entity_id)
);

-- ---------------------------------------------------------------------------
-- Filings seen in registries (written by the n8n registry monitor)
-- ---------------------------------------------------------------------------

create table if not exists filing (
  id               bigserial primary key,
  filing_uid       text unique not null,          -- registry:entity:id — dedupe key
  registry         text not null,
  entity_id        text not null,
  legal_entity_id  int references legal_entity on delete set null,
  filed_at         date,
  form             text,
  category         text,                          -- accounts | interim | event | officers | capital | charges | insolvency | ownership | other
  description      text,
  period_end       date,
  document_url     text,
  source_url       text,
  status           text not null default 'new'
                   check (status in ('new', 'reviewed', 'extracted', 'published', 'ignored')),
  inserted_at      timestamptz default now()
);
create index if not exists ix_filing_entity_date on filing (legal_entity_id, filed_at desc);
create index if not exists ix_filing_status on filing (status, inserted_at desc);

-- ---------------------------------------------------------------------------
-- Numbers taken out of filings (manual or LLM-extracted, always traceable)
-- ---------------------------------------------------------------------------

create table if not exists financial_fact (
  id               bigserial primary key,
  legal_entity_id  int not null references legal_entity on delete cascade,
  period_end       date not null,
  metric           text not null,                 -- revenue | ebitda | operating_result | net_result | equity | total_assets | employees | ...
  value            numeric not null,
  currency         text,                          -- ISO-4217; null for headcounts
  scale            int not null default 1,        -- 1, 1000 (tDKK), 1000000
  segment          text,                          -- 'cs2', 'north_america', null = whole entity
  filing_id        bigint references filing on delete set null,
  page             int,                           -- page in the source PDF
  reliability      char(1) not null default 'A'
                   check (reliability in ('A', 'B', 'C')),  -- A document, B direct statement, C rumour/estimate
  extracted_by     text not null default 'manual'
                   check (extracted_by in ('manual', 'llm', 'registry_api')),
  verified         boolean not null default false,
  notes            text,
  created_at       timestamptz default now(),
  unique (legal_entity_id, period_end, metric, segment)
);

-- ---------------------------------------------------------------------------
-- Row level security: only service_role (n8n / parser) may read or write.
-- ---------------------------------------------------------------------------

alter table org_brand      enable row level security;
alter table legal_entity   enable row level security;
alter table brand_entity   enable row level security;
alter table filing         enable row level security;
alter table financial_fact enable row level security;

-- ---------------------------------------------------------------------------
-- Seed: brands and the entities with verified registry ids (Oct 2026)
-- ---------------------------------------------------------------------------

insert into org_brand (slug, name, kind) values
  ('blast',    'BLAST',              'tournament_operator'),
  ('efg',      'ESL FACEIT Group',   'tournament_operator'),
  ('fnatic',   'Fnatic',             'team'),
  ('astralis', 'Astralis',           'team'),
  ('heroic',   'HEROIC',             'team'),
  ('nip',      'Ninjas in Pyjamas',  'team'),
  ('faze',     'FaZe Clan',          'team')
on conflict (slug) do nothing;

insert into legal_entity (registry, entity_id, name, country, notes) values
  ('dk_cvr',   '38074466', 'BLAST ApS',                 'DK', 'formerly RFRSH Entertainment ApS'),
  ('gb_ch',    '07751649', 'ESL FACEIT GROUP LIMITED',  'GB', 'UK entity of Savvy Games Group (PIF)'),
  ('gb_ch',    '07805670', 'FNATIC LTD',                'GB', null),
  ('dk_cvr',   '37275506', 'Astralis CS ApS',           'DK', 'CS subsidiary; parent Astralis A/S CVR still to add'),
  ('no_brreg', '924115904','HEROIC AS',                 'NO', null),
  ('us_sec',   '1966233',  'NIP Group Inc.',            'US', 'Nasdaq: NIPG, files 20-F / 6-K'),
  ('us_sec',   '1714562',  'GameSquare Holdings, Inc.', 'US', 'Nasdaq: GAME, owns FaZe')
on conflict (registry, entity_id) do nothing;

insert into brand_entity (brand_id, legal_entity_id, relation, source)
select b.id, e.id, m.relation, 'registry'
from (values
  ('blast',    'dk_cvr',   '38074466', 'operating'),
  ('efg',      'gb_ch',    '07751649', 'operating'),
  ('fnatic',   'gb_ch',    '07805670', 'operating'),
  ('astralis', 'dk_cvr',   '37275506', 'subsidiary'),
  ('heroic',   'no_brreg', '924115904','operating'),
  ('nip',      'us_sec',   '1966233',  'holding'),
  ('faze',     'us_sec',   '1714562',  'parent')
) as m(slug, registry, entity_id, relation)
join org_brand b on b.slug = m.slug
join legal_entity e on e.registry = m.registry and e.entity_id = m.entity_id
on conflict do nothing;
