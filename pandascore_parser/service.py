"""PandaScore CS2 service layer.

Maps PandaScore JSON → row dicts shaped for our Postgres schema.

Endpoint paths (all under /csgo/* per PandaScore's historical naming
which they kept after the CS2 rename):

    GET /csgo/matches/upcoming       — scheduled, not yet started
    GET /csgo/matches/past           — finished
    GET /csgo/tournaments/upcoming   — running + scheduled
    GET /csgo/tournaments/running
    GET /csgo/teams                  — paginated team catalogue
    GET /csgo/players                — paginated player catalogue

Each PandaScore object carries an integer ``id`` we use as
``source_id`` (cast to text per the schema).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .client import PandaScoreClient

log = logging.getLogger(__name__)


def _iso_or_none(value: Any) -> Optional[str]:
    if not value:
        return None
    s = str(value)
    try:
        # PandaScore returns ISO 8601 with 'Z' suffix.
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _date_or_none(value: Any) -> Optional[str]:
    iso = _iso_or_none(value)
    return iso[:10] if iso else None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _money_or_none(value: Any) -> Optional[float]:
    """PandaScore prize pool is a free-text string like '$250,000'."""
    if value in (None, ""):
        return None
    s = str(value).replace(",", "").replace("$", "").strip()
    try:
        return float(s.split()[0])
    except (TypeError, ValueError, IndexError):
        return None


class PandaScoreService:
    def __init__(self, client: Optional[PandaScoreClient] = None):
        self.client = client or PandaScoreClient()

    # ---- matches --------------------------------------------------------

    def matches(
        self,
        *,
        endpoint: str,                 # 'upcoming' or 'past'
        days_window: int = 7,
        per_page: int = 50,
        max_pages: int = 10,
    ) -> list[dict]:
        """Fetch CS matches in a rolling window. Endpoint chooses
        upcoming or past."""
        if endpoint not in ("upcoming", "past"):
            raise ValueError("endpoint must be 'upcoming' or 'past'")

        # PandaScore range filter: range[scheduled_at]=ISO,ISO
        now = datetime.now(timezone.utc)
        if endpoint == "upcoming":
            start, end = now, now + timedelta(days=days_window)
        else:
            start, end = now - timedelta(days=days_window), now
        params = {
            "range[scheduled_at]": f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')},"
                                   f"{end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "sort": "scheduled_at",
        }

        rows = list(self.client.paginate(
            f"/csgo/matches/{endpoint}",
            params=params,
            per_page=per_page,
            max_pages=max_pages,
        ))
        out = [self._match_row(m) for m in rows]
        log.info(
            "event=pandascore_matches endpoint=%s window_days=%d count=%d",
            endpoint, days_window, len(out),
        )
        return out

    @staticmethod
    def _match_row(m: dict) -> dict:
        opps = m.get("opponents") or []
        team_a = (opps[0].get("opponent", {}) or {}).get("name") if len(opps) >= 1 else None
        team_b = (opps[1].get("opponent", {}) or {}).get("name") if len(opps) >= 2 else None
        results = m.get("results") or []
        score_a = score_b = None
        if len(results) >= 2:
            score_a = _int_or_none(results[0].get("score"))
            score_b = _int_or_none(results[1].get("score"))
        status_map = {
            "not_started": "not_started",
            "running": "live",
            "finished": "finished",
            "canceled": "cancelled",
            "postponed": "not_started",
        }
        status = status_map.get(m.get("status") or "", "not_started")
        return {
            "source": "pandascore",
            "source_id": str(m.get("id")),
            "team_a": team_a,
            "team_b": team_b,
            "score_a": score_a,
            "score_b": score_b,
            "scheduled_at": _iso_or_none(m.get("scheduled_at")),
            "status": status,
            "raw_payload": m,
            "_tournament_pandascore_id": (
                str(m["tournament"].get("id"))
                if m.get("tournament") and m["tournament"].get("id") is not None
                else None
            ),
        }

    # ---- tournaments ----------------------------------------------------

    def tournaments(
        self,
        *,
        endpoint: str = "running",     # 'running' or 'upcoming' or 'past'
        per_page: int = 50,
        max_pages: int = 6,
    ) -> list[dict]:
        if endpoint not in ("running", "upcoming", "past"):
            raise ValueError("endpoint must be 'running', 'upcoming', or 'past'")
        rows = list(self.client.paginate(
            f"/csgo/tournaments/{endpoint}",
            params={"sort": "-begin_at"},
            per_page=per_page,
            max_pages=max_pages,
        ))
        out = []
        for t in rows:
            league = t.get("league") or {}
            serie = t.get("serie") or {}
            name_parts = [
                league.get("name") or "",
                serie.get("full_name") or serie.get("name") or "",
                t.get("name") or "",
            ]
            name = " — ".join(p for p in name_parts if p) or f"Tournament {t.get('id')}"
            sdate = _date_or_none(t.get("begin_at"))
            edate = _date_or_none(t.get("end_at"))
            today = date.today().isoformat()
            if edate and edate < today:
                status = "finished"
            elif sdate and sdate > today:
                status = "upcoming"
            else:
                status = "ongoing"
            out.append({
                "source": "pandascore",
                "source_id": str(t.get("id")),
                "name": name,
                "tier": (t.get("tier") or league.get("tier") or None),
                "start_date": sdate,
                "end_date": edate,
                "prize_pool_usd": _money_or_none(t.get("prizepool")),
                "location": t.get("country") or t.get("region") or None,
                "status": status,
                "raw_payload": t,
            })
        log.info(
            "event=pandascore_tournaments endpoint=%s count=%d", endpoint, len(out)
        )
        return out

    # ---- teams / players -----------------------------------------------

    def teams_seen_in(self, matches: Iterable[dict]) -> list[dict]:
        """Build teams_meta rows from raw match payloads we already have.
        Avoids a separate /csgo/teams paginated walk on the first run."""
        seen: dict[str, dict] = {}
        for m in matches:
            payload = m.get("raw_payload") or {}
            for opp in payload.get("opponents") or []:
                team = opp.get("opponent") or {}
                tid = team.get("id")
                if tid is None:
                    continue
                key = str(tid)
                if key in seen:
                    continue
                seen[key] = {
                    "source": "pandascore",
                    "source_id": key,
                    "name": team.get("name") or f"Team {tid}",
                    "region": team.get("location") or None,
                    "raw_payload": team,
                }
        log.info("event=pandascore_teams count=%d", len(seen))
        return list(seen.values())

    def players_seen_in(self, matches: Iterable[dict]) -> list[dict]:
        """Build players_meta rows from raw match payloads. Some endpoints
        nest players under each opponent; if absent we just return []."""
        seen: dict[str, dict] = {}
        for m in matches:
            payload = m.get("raw_payload") or {}
            for opp in payload.get("opponents") or []:
                team = opp.get("opponent") or {}
                team_id_str = str(team.get("id")) if team.get("id") is not None else None
                for p in team.get("players") or []:
                    pid = p.get("id")
                    if pid is None:
                        continue
                    key = str(pid)
                    if key in seen:
                        continue
                    seen[key] = {
                        "source": "pandascore",
                        "source_id": key,
                        "nickname": p.get("name") or f"Player {pid}",
                        "real_name": (
                            ((p.get("first_name") or "") + " " + (p.get("last_name") or "")).strip()
                            or None
                        ),
                        "_team_pandascore_id": team_id_str,
                        "raw_payload": p,
                    }
        log.info("event=pandascore_players count=%d", len(seen))
        return list(seen.values())
