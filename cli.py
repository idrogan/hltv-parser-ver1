"""Command-line interface across all data sources.

Every subcommand prints a single JSON document to stdout, so it can be
piped straight into n8n's Execute Command node, jq, a cron+tee log, or
a Supabase row insert.

Examples
--------
    python cli.py hltv team-maps 4608 natus-vincere --months-back 5
    python cli.py hltv rankings

    python cli.py steam price "Sticker | Titan (Holo) | Katowice 2014"
    python cli.py steam search "Katowice 2014 Holo" --count 30
    python cli.py steam history "AK-47 | Redline (Field-Tested)"   # requires cookie

    python cli.py escharts tournaments --game cs2
    python cli.py escharts tournament cs2 iem-katowice-2024
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def _print(out) -> int:
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


def _add_window(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", dest="start_date", help="YYYY-MM-DD")
    p.add_argument("--end", dest="end_date", help="YYYY-MM-DD")
    p.add_argument("--months-back", dest="months_back", type=int)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="esports-data")
    parser.add_argument("--log-level", default="WARNING")
    sources = parser.add_subparsers(dest="source", required=True)

    hltv = sources.add_parser("hltv", help="HLTV.org")
    hltv.add_argument("--min-delay", type=float, default=2.0)
    hltv.add_argument("--proxy", default=None)
    h = hltv.add_subparsers(dest="cmd", required=True)

    p = h.add_parser("team"); p.add_argument("team_id", type=int); p.add_argument("slug"); _add_window(p)
    p = h.add_parser("team-maps"); p.add_argument("team_id", type=int); p.add_argument("slug"); _add_window(p)
    p = h.add_parser("team-matches"); p.add_argument("team_id", type=int); p.add_argument("slug"); _add_window(p)
    p = h.add_parser("player"); p.add_argument("player_id", type=int); p.add_argument("slug"); _add_window(p)
    p = h.add_parser("search-team"); p.add_argument("name")
    h.add_parser("rankings")
    h.add_parser("upcoming")
    p = h.add_parser("results"); p.add_argument("--offset", type=int, default=0)

    steam = sources.add_parser("steam", help="Steam Community Market")
    steam.add_argument("--min-delay", type=float, default=3.5)
    steam.add_argument("--proxy", default=None)
    steam.add_argument(
        "--login-secure",
        default=os.getenv("STEAM_LOGIN_SECURE"),
        help="steamLoginSecure cookie (only needed for 'history')",
    )
    s = steam.add_subparsers(dest="cmd", required=True)

    p = s.add_parser("price", help="Current price + 24h sold volume")
    p.add_argument("market_hash_name")
    p.add_argument("--appid", type=int, default=730)
    p.add_argument("--currency", type=int, default=1)

    p = s.add_parser("search")
    p.add_argument("query")
    p.add_argument("--appid", type=int, default=730)
    p.add_argument("--count", type=int, default=20)
    p.add_argument("--start", type=int, default=0)

    p = s.add_parser("history", help="Full per-sale history (needs cookie)")
    p.add_argument("market_hash_name")
    p.add_argument("--appid", type=int, default=730)

    esc = sources.add_parser("escharts", help="EsportsCharts.com viewership")
    esc.add_argument("--min-delay", type=float, default=2.0)
    esc.add_argument("--proxy", default=None)
    e = esc.add_subparsers(dest="cmd", required=True)

    p = e.add_parser("tournaments")
    p.add_argument("--game", default="cs2")
    p.add_argument("--year", type=int, default=None)

    p = e.add_parser("tournament")
    p.add_argument("game")
    p.add_argument("slug")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level)

    if args.source == "hltv":
        from hltv_parser import HLTVClient, HLTVService
        from hltv_parser._flag import is_enabled as _hltv_enabled
        if not _hltv_enabled():
            logging.getLogger("hltv_parser._flag").warning(
                "event=hltv_paused entry_point=cli.hltv.%s reason=HLTV_ENABLED_false",
                args.cmd,
            )
            print(
                json.dumps(
                    {"error": "hltv_paused", "detail": "Set HLTV_ENABLED=true to re-enable."}
                ),
                file=sys.stderr,
            )
            return 3
        svc = HLTVService(HLTVClient(min_delay=args.min_delay, proxy=args.proxy))
        if args.cmd == "team":
            return _print(svc.team_overview(args.team_id, args.slug, args.start_date, args.end_date, args.months_back))
        if args.cmd == "team-maps":
            return _print(svc.team_map_stats(args.team_id, args.slug, args.start_date, args.end_date, args.months_back))
        if args.cmd == "team-matches":
            return _print(svc.team_matches(args.team_id, args.slug, args.start_date, args.end_date, args.months_back))
        if args.cmd == "player":
            return _print(svc.player_stats(args.player_id, args.slug, args.start_date, args.end_date, args.months_back))
        if args.cmd == "search-team":
            return _print({"results": svc.find_team(args.name)})
        if args.cmd == "rankings":
            return _print({"rankings": svc.rankings()})
        if args.cmd == "upcoming":
            return _print({"matches": svc.upcoming_matches()})
        if args.cmd == "results":
            return _print({"results": svc.results(offset=args.offset)})

    if args.source == "steam":
        from steam_market import SteamClient, SteamMarketService
        client = SteamClient(
            min_delay=args.min_delay,
            proxy=args.proxy,
            login_secure_cookie=args.login_secure,
        )
        svc = SteamMarketService(client)
        if args.cmd == "price":
            return _print(svc.price_overview(args.market_hash_name, appid=args.appid, currency=args.currency))
        if args.cmd == "search":
            return _print(svc.search(args.query, appid=args.appid, count=args.count, start=args.start))
        if args.cmd == "history":
            return _print(svc.price_history(args.market_hash_name, appid=args.appid))

    if args.source == "escharts":
        from escharts_parser import EsChartsClient, EsChartsService
        svc = EsChartsService(EsChartsClient(min_delay=args.min_delay, proxy=args.proxy))
        if args.cmd == "tournaments":
            return _print({
                "game": args.game,
                "year": args.year,
                "tournaments": svc.tournaments(game=args.game, year=args.year),
            })
        if args.cmd == "tournament":
            return _print(svc.tournament(game=args.game, slug=args.slug))

    print(f"unknown command: {args}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
