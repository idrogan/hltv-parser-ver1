"""End-to-end Liquipedia run.

  1. Open SupabaseWriter + LiquipediaClient.
  2. Insert a `_scraper_runs` row with status='running'.
  3. Fetch tier 1/2 tournaments, upsert into `tournaments`.
  4. For each tournament, fetch prize distribution and match results,
     write to their tables.
  5. Finalise the `_scraper_runs` row with status='ok' or 'error'.

This module is import-safe: it does not touch network or DB at import
time. Call ``run_once()`` to actually execute.
"""
from __future__ import annotations

import logging
import traceback
from typing import Optional

from common.sb_writer import SupabaseWriter
from .client import LiquipediaClient
from .service import LiquipediaService

log = logging.getLogger(__name__)


def run_once(
    *,
    tier_max: int = 2,
    months_back: int = 12,
    months_forward: int = 6,
    tournaments_limit: int = 100,
    fetch_prizes: bool = True,
    fetch_matches: bool = True,
    max_tournaments_for_detail: int = 20,
) -> dict:
    """Run the full Liquipedia pipeline once. Returns a summary dict.

    Parameters
    ----------
    max_tournaments_for_detail:
        Cap on how many tournaments we drill into for prizes + matches
        in a single run. Avoids hammering the API on the very first
        run; raise once we've confirmed quotas hold.
    """
    writer = SupabaseWriter()
    client = LiquipediaClient()
    svc = LiquipediaService(client)

    rows_written = 0
    tournaments_count = 0
    prizes_count = 0
    matches_count = 0
    run_id = writer.start_run(
        "liquipedia",
        meta={
            "tier_max": tier_max,
            "months_back": months_back,
            "months_forward": months_forward,
        },
    )

    try:
        # Step 1: tournaments
        tournaments = svc.recent_tournaments(
            tier_max=tier_max,
            months_back=months_back,
            months_forward=months_forward,
            limit=tournaments_limit,
        )
        if tournaments:
            written = writer.upsert(
                "tournaments",
                tournaments,
                on_conflict="source,source_id",
            )
            tournaments_count = len(written)
            rows_written += tournaments_count

            # Build PageName -> tournament_id index for FKs.
            page_to_id = {row["source_id"]: row["id"] for row in written}
        else:
            page_to_id = {}

        # Step 2 + 3: prize distribution + matches per tournament.
        # Drill into the most recent N to bound API load.
        detail_pages = list(page_to_id.keys())[:max_tournaments_for_detail]

        if fetch_prizes:
            for page in detail_pages:
                tid = page_to_id[page]
                try:
                    placements = svc.prize_distribution(page)
                except Exception as e:
                    log.warning(
                        "event=liquipedia_prize_skip page=%s error=%r", page, e
                    )
                    continue
                if not placements:
                    continue
                rows = [{**p, "tournament_id": tid} for p in placements]
                # No unique key on tournament_prize_distribution, so
                # delete-then-insert per tournament keeps re-runs idempotent.
                writer.delete(
                    "tournament_prize_distribution", {"tournament_id": tid}
                )
                inserted = writer.insert("tournament_prize_distribution", rows)
                prizes_count += len(inserted)
                rows_written += len(inserted)

        if fetch_matches:
            for page in detail_pages:
                tid = page_to_id[page]
                try:
                    matches = svc.match_results(page)
                except Exception as e:
                    log.warning(
                        "event=liquipedia_matches_skip page=%s error=%r", page, e
                    )
                    continue
                if not matches:
                    continue
                rows = [{**m, "tournament_id": tid} for m in matches]
                inserted = writer.upsert(
                    "matches", rows, on_conflict="source,source_id"
                )
                matches_count += len(inserted)
                rows_written += len(inserted)

        writer.finish_run_ok(
            run_id,
            rows_written=rows_written,
            meta={
                "tournaments": tournaments_count,
                "prize_rows": prizes_count,
                "match_rows": matches_count,
                "detail_pages": detail_pages,
            },
        )
        return {
            "ok": True,
            "run_id": run_id,
            "tournaments": tournaments_count,
            "prize_rows": prizes_count,
            "match_rows": matches_count,
            "rows_written": rows_written,
        }
    except Exception as e:
        tb = traceback.format_exc()
        writer.finish_run_error(run_id, error=tb, rows_written=rows_written)
        log.exception("event=liquipedia_run_failed run_id=%s", run_id)
        return {
            "ok": False,
            "run_id": run_id,
            "error": str(e),
            "rows_written": rows_written,
        }
    finally:
        client.close()
        writer.close()
