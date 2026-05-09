"""End-to-end PandaScore run.

Order of operations matters for FKs:
  1. tournaments  → upsert; build pandascore_id → tournament.id index
  2. matches      → upsert with tournament_id resolved from index
  3. teams_meta   → upsert from match payloads, build team index
  4. players_meta → upsert from match payloads, link team_id

Everything is bracketed by a `_scraper_runs` row.
"""
from __future__ import annotations

import logging
import traceback
from typing import Optional

from common.sb_writer import SupabaseWriter
from .client import PandaScoreClient, PandaScoreError
from .service import PandaScoreService

log = logging.getLogger(__name__)


def run_once(
    *,
    matches_window_days: int = 7,
    matches_max_pages: int = 6,
    tournaments_max_pages: int = 4,
    fetch_past_matches: bool = True,
    fetch_upcoming_matches: bool = True,
    fetch_running_tournaments: bool = True,
    fetch_upcoming_tournaments: bool = True,
) -> dict:
    writer = SupabaseWriter()
    client = PandaScoreClient()
    svc = PandaScoreService(client)

    rows_written = 0
    summary = {
        "tournaments": 0,
        "matches": 0,
        "teams": 0,
        "players": 0,
    }

    run_id = writer.start_run(
        "pandascore",
        meta={
            "matches_window_days": matches_window_days,
            "matches_max_pages": matches_max_pages,
            "tournaments_max_pages": tournaments_max_pages,
        },
    )

    try:
        # 1. Tournaments — upsert and build {pandascore_id: tournaments.id}.
        all_tournaments: list[dict] = []
        if fetch_running_tournaments:
            all_tournaments += svc.tournaments(
                endpoint="running", max_pages=tournaments_max_pages
            )
        if fetch_upcoming_tournaments:
            all_tournaments += svc.tournaments(
                endpoint="upcoming", max_pages=tournaments_max_pages
            )

        tournament_index: dict[str, int] = {}
        if all_tournaments:
            written = writer.upsert(
                "tournaments",
                all_tournaments,
                on_conflict="source,source_id",
            )
            for row in written:
                tournament_index[row["source_id"]] = row["id"]
            summary["tournaments"] = len(written)
            rows_written += len(written)

        # 2. Matches — upcoming + past — and resolve tournament FK from index.
        all_match_rows: list[dict] = []
        if fetch_upcoming_matches:
            all_match_rows += svc.matches(
                endpoint="upcoming",
                days_window=matches_window_days,
                max_pages=matches_max_pages,
            )
        if fetch_past_matches:
            all_match_rows += svc.matches(
                endpoint="past",
                days_window=matches_window_days,
                max_pages=matches_max_pages,
            )

        if all_match_rows:
            # Pop the helper field that doesn't belong in the matches table.
            db_match_rows = []
            for m in all_match_rows:
                ps_tid = m.pop("_tournament_pandascore_id", None)
                local_tid = tournament_index.get(ps_tid) if ps_tid else None
                m_for_db = {**m, "tournament_id": local_tid}
                db_match_rows.append(m_for_db)
            written = writer.upsert(
                "matches", db_match_rows, on_conflict="source,source_id"
            )
            summary["matches"] = len(written)
            rows_written += len(written)

        # 3. Teams — derived from match payloads (no extra paginated walk).
        team_rows = svc.teams_seen_in(all_match_rows)
        team_index: dict[str, int] = {}
        if team_rows:
            written = writer.upsert(
                "teams_meta", team_rows, on_conflict="source,source_id"
            )
            for row in written:
                team_index[row["source_id"]] = row["id"]
            summary["teams"] = len(written)
            rows_written += len(written)

        # 4. Players — derived from match payloads, linked to team via index.
        player_rows = svc.players_seen_in(all_match_rows)
        if player_rows:
            db_player_rows = []
            for p in player_rows:
                ps_team_id = p.pop("_team_pandascore_id", None)
                local_team_id = team_index.get(ps_team_id) if ps_team_id else None
                db_player_rows.append({**p, "team_id": local_team_id})
            written = writer.upsert(
                "players_meta", db_player_rows, on_conflict="source,source_id"
            )
            summary["players"] = len(written)
            rows_written += len(written)

        meta = {
            **summary,
            "rate_limit_limit": client.rate_limit_limit,
            "rate_limit_remaining": client.rate_limit_remaining,
        }
        writer.finish_run_ok(run_id, rows_written=rows_written, meta=meta)
        return {"ok": True, "run_id": run_id, "rows_written": rows_written, **summary}

    except Exception as e:
        tb = traceback.format_exc()
        writer.finish_run_error(
            run_id,
            error=tb,
            rows_written=rows_written,
            meta={
                **summary,
                "rate_limit_remaining": client.rate_limit_remaining,
            },
        )
        log.exception("event=pandascore_run_failed run_id=%s", run_id)
        return {"ok": False, "run_id": run_id, "error": str(e), **summary}
    finally:
        client.close()
        writer.close()
