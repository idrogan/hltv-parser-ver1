"""Command-line interface for the HLTV parser.

Designed for ad-hoc runs and N8N's "Execute Command" node — every
sub-command prints a single JSON document to stdout, so it can be piped
straight into ``$json`` in N8N or a Make HTTP module's parse-JSON step.

Examples
--------
    python cli.py team-maps 4608 natus-vincere --months-back 5
    python cli.py player 7998 s1mple --start 2025-11-01 --end 2026-04-01
    python cli.py rankings
    python cli.py upcoming
    python cli.py results
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from hltv_parser import HLTVClient, HLTVService


def _add_window(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", dest="start_date", help="YYYY-MM-DD")
    p.add_argument("--end", dest="end_date", help="YYYY-MM-DD")
    p.add_argument("--months-back", dest="months_back", type=int)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hltv-parser")
    parser.add_argument("--min-delay", type=float, default=2.0)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--log-level", default="WARNING")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_overview = sub.add_parser("team", help="Team overview stats")
    p_overview.add_argument("team_id", type=int)
    p_overview.add_argument("slug")
    _add_window(p_overview)

    p_maps = sub.add_parser("team-maps", help="Per-map stats incl. CT/T round winrate")
    p_maps.add_argument("team_id", type=int)
    p_maps.add_argument("slug")
    _add_window(p_maps)

    p_tmatches = sub.add_parser("team-matches", help="Team match history")
    p_tmatches.add_argument("team_id", type=int)
    p_tmatches.add_argument("slug")
    _add_window(p_tmatches)

    p_player = sub.add_parser("player", help="Player stats")
    p_player.add_argument("player_id", type=int)
    p_player.add_argument("slug")
    _add_window(p_player)

    p_search = sub.add_parser("search-team", help="Resolve a team name to id+slug")
    p_search.add_argument("name")

    sub.add_parser("rankings", help="World ranking top 30")
    sub.add_parser("upcoming", help="Upcoming matches")

    p_results = sub.add_parser("results", help="Recent match results")
    p_results.add_argument("--offset", type=int, default=0)

    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level)

    service = HLTVService(HLTVClient(min_delay=args.min_delay, proxy=args.proxy))

    if args.cmd == "team":
        out = service.team_overview(args.team_id, args.slug, args.start_date, args.end_date, args.months_back)
    elif args.cmd == "team-maps":
        out = service.team_map_stats(args.team_id, args.slug, args.start_date, args.end_date, args.months_back)
    elif args.cmd == "team-matches":
        out = service.team_matches(args.team_id, args.slug, args.start_date, args.end_date, args.months_back)
    elif args.cmd == "player":
        out = service.player_stats(args.player_id, args.slug, args.start_date, args.end_date, args.months_back)
    elif args.cmd == "search-team":
        out = {"results": service.find_team(args.name)}
    elif args.cmd == "rankings":
        out = {"rankings": service.rankings()}
    elif args.cmd == "upcoming":
        out = {"matches": service.upcoming_matches()}
    elif args.cmd == "results":
        out = {"results": service.results(offset=args.offset)}
    else:
        parser.error(f"unknown command {args.cmd}")
        return 2

    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
