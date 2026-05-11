-- ============================================================================
--  0002_sticker_history — DOWN
--
--  Drops Phase-2 sticker history schema. Idempotent.
-- ============================================================================

begin;

drop view  if exists sticker_price_history_blended;
drop table if exists sticker_price_history;
drop table if exists sticker_catalog;

commit;
