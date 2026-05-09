"""Liquipedia service layer.

Maps Cargo queries → row dicts shaped for our Postgres schema. Three
queries cover Phase 1:

  1. Recent + upcoming tier 1/2 tournaments → ``tournaments``
  2. Per-tournament prize distribution → ``tournament_prize_distribution``
  3. Per-tournament match results → ``matches``

Cargo schema reference: https://liquipedia.net/counterstrike/Special:CargoTables
Field names below are best-effort as of 2026-05-09. If a query returns
empty / errors, run the query interactively at
https://liquipedia.net/counterstrike/Special:CargoQuery to discover
the current field set, then update here.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Iterable, Optional

from .client import LiquipediaClient

log = logging.getLogger(__name__)


def _parse_date(value: Any) -> Optional[str]:
    """Liquipedia returns dates as 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'."""
    if not value:
        return None
    s = str(value).strip()
    if not s or s.startswith("1970-01-01") or s == "1900-01-01":
        return None
    return s.split(" ")[0][:10]


def _parse_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _parse_money(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _status_for_dates(start: Optional[str], end: Optional[str]) -> str:
    today = date.today().isoformat()
    if end and end < today:
        return "finished"
    if start and start > today:
        return "upcoming"
    return "ongoing"


class LiquipediaService:
    def __init__(self, client: Optional[LiquipediaClient] = None):
        self.client = client or LiquipediaClient()

    # ---- 1. Tournaments --------------------------------------------------

    def recent_tournaments(
        self,
        tier_max: int = 2,
        months_back: int = 12,
        months_forward: int = 6,
        limit: int = 100,
    ) -> list[dict]:
        """Tier 1/2 CS tournaments in a window around today.

        Returns row dicts already shaped for the ``tournaments`` table:
            source, source_id, name, tier, start_date, end_date,
            prize_pool_usd, location, status, raw_payload
        """
        # Liquipediatier is stored as a string. Build IN-clause.
        tier_values = ",".join(f'"{t}"' for t in range(1, tier_max + 1))
        today = date.today()
        # Approximate windowing in SQL: subtract months_back * 30 days.
        from datetime import timedelta
        start_window = (today - timedelta(days=months_back * 31)).isoformat()
        end_window = (today + timedelta(days=months_forward * 31)).isoformat()

        rows = self.client.cargoquery(
            tables="Tournaments",
            fields=(
                "Tournaments.PageName=PageName,"
                "Tournaments.Name=Name,"
                "Tournaments.Liquipediatier=Tier,"
                "Tournaments.Liquipediatiertype=TierType,"
                "Tournaments.Sdate=Sdate,"
                "Tournaments.Edate=Edate,"
                "Tournaments.Prizepool=Prizepool,"
                "Tournaments.Location=Location,"
                "Tournaments.Status=Status,"
                "Tournaments.Series=Series"
            ),
            where=(
                f'Tournaments.Liquipediatier IN ({tier_values}) '
                f'AND Tournaments.Sdate >= "{start_window}" '
                f'AND Tournaments.Sdate <= "{end_window}"'
            ),
            order_by="Tournaments.Sdate DESC",
            limit=limit,
        )

        out = []
        for r in rows:
            page = (r.get("PageName") or "").replace(" ", "_")
            if not page:
                continue
            sdate = _parse_date(r.get("Sdate"))
            edate = _parse_date(r.get("Edate"))
            status = (r.get("Status") or "").strip().lower() or _status_for_dates(sdate, edate)
            out.append({
                "source": "liquipedia",
                "source_id": page,
                "name": r.get("Name") or page.replace("_", " "),
                "tier": r.get("Tier") or None,
                "start_date": sdate,
                "end_date": edate,
                "prize_pool_usd": _parse_money(r.get("Prizepool")),
                "location": r.get("Location") or None,
                "status": status,
                "raw_payload": r,
            })
        log.info("event=liquipedia_tournaments count=%d", len(out))
        return out

    # ---- 2. Prize distribution -------------------------------------------

    def prize_distribution(self, page_name: str) -> list[dict]:
        """Placements for one tournament page.

        Cargo table ``Placement`` (singular on Liquipedia) holds
        per-team / per-player results. Field names vary; we ask for the
        common ones.
        """
        page = page_name.replace("_", " ")
        rows = self.client.cargoquery(
            tables="Placement",
            fields=(
                "Placement.PageName=PageName,"
                "Placement.Place=Place,"
                "Placement.Participant=Participant,"
                "Placement.Prizemoney=Prizemoney,"
                "Placement.Prizepoolusd=Prizepoolusd,"
                "Placement.Points=Points"
            ),
            where=f'Placement.PageName="{page}"',
            order_by="Placement.Place ASC",
            limit=50,
        )
        out = []
        for r in rows:
            participant = (r.get("Participant") or "").strip()
            place = (r.get("Place") or "").strip()
            if not participant or not place:
                continue
            out.append({
                "place": place,
                "team_or_player": participant,
                "amount_usd": _parse_money(r.get("Prizepoolusd") or r.get("Prizemoney")),
                "points": _parse_money(r.get("Points")),
                "notes": None,
            })
        log.info(
            "event=liquipedia_prize_dist page=%s count=%d", page_name, len(out)
        )
        return out

    # ---- 3. Match results ------------------------------------------------

    def match_results(self, page_name: str, limit: int = 200) -> list[dict]:
        """Matches played within one tournament page.

        Cargo table ``MatchScheduleAll`` contains scheduled & completed
        matches with team/score/date. Some wikis use ``Match`` instead;
        we try the most common name.
        """
        page = page_name.replace("_", " ")
        rows = self.client.cargoquery(
            tables="MatchScheduleAll",
            fields=(
                "MatchScheduleAll.PageName=PageName,"
                "MatchScheduleAll.MatchId=MatchId,"
                "MatchScheduleAll.Tournament=Tournament,"
                "MatchScheduleAll.Opponent1=TeamA,"
                "MatchScheduleAll.Opponent2=TeamB,"
                "MatchScheduleAll.Opponent1Score=ScoreA,"
                "MatchScheduleAll.Opponent2Score=ScoreB,"
                "MatchScheduleAll.Date=Scheduled,"
                "MatchScheduleAll.Winner=Winner"
            ),
            where=f'MatchScheduleAll.PageName="{page}"',
            order_by="MatchScheduleAll.Date DESC",
            limit=limit,
        )
        out = []
        for r in rows:
            mid = (r.get("MatchId") or "").strip()
            if not mid:
                # Fall back to a synthetic id using PageName + scheduled time
                mid = f"{r.get('PageName')}::{r.get('Scheduled')}::{r.get('TeamA')}::{r.get('TeamB')}"
            sched_raw = r.get("Scheduled") or ""
            scheduled_at: Optional[str] = None
            if sched_raw:
                try:
                    scheduled_at = datetime.fromisoformat(
                        str(sched_raw).replace(" ", "T")
                    ).isoformat()
                except ValueError:
                    scheduled_at = None
            score_a = _parse_int(r.get("ScoreA"))
            score_b = _parse_int(r.get("ScoreB"))
            status = "finished" if score_a is not None and score_b is not None else "not_started"
            out.append({
                "source": "liquipedia",
                "source_id": str(mid),
                "team_a": (r.get("TeamA") or None),
                "team_b": (r.get("TeamB") or None),
                "score_a": score_a,
                "score_b": score_b,
                "scheduled_at": scheduled_at,
                "status": status,
                "raw_payload": r,
            })
        log.info(
            "event=liquipedia_matches page=%s count=%d", page_name, len(out)
        )
        return out
