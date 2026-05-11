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

    p = s.add_parser("seed", help="Bulk add stickers to watched_items (read names from stdin or file, one per line)")
    p.add_argument("--file", default=None, help="Path to a newline-separated names file. Default: stdin.")
    p.add_argument("--event-slug", default=None, help="Tag every seeded item with this slug (e.g. 'iem-cologne-major-2026').")
    p.add_argument("--category", default="sticker")
    p.add_argument("--appid", type=int, default=730)

    p = s.add_parser("refresh", help="Walk watched_items, write fresh prices to steam_prices")
    p.add_argument("--event-slug", default=None, help="Filter watched_items by event_slug")
    p.add_argument("--appid", type=int, default=None)
    p.add_argument("--currency", type=int, default=1)
    p.add_argument("--max-items", type=int, default=200)

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

    ps = sources.add_parser("pandascore", help="PandaScore CS2 API")
    psc = ps.add_subparsers(dest="cmd", required=True)
    p = psc.add_parser("run", help="End-to-end pull → Supabase write")
    p.add_argument("--days", type=int, default=7, dest="matches_window_days")
    p.add_argument("--matches-pages", type=int, default=6, dest="matches_max_pages")
    p.add_argument("--tournaments-pages", type=int, default=4, dest="tournaments_max_pages")
    p.add_argument("--no-past-matches", action="store_true")
    p.add_argument("--no-upcoming-matches", action="store_true")
    p.add_argument("--no-running-tournaments", action="store_true")
    p.add_argument("--no-upcoming-tournaments", action="store_true")

    sh = sources.add_parser("steam-history",
                            help="Steam Market lifetime price history (sticker_price_history table)")
    shc = sh.add_subparsers(dest="cmd", required=True)
    p = shc.add_parser("backfill",
                       help="Walk a catalog (or --names list) and upsert daily price history rows")
    p.add_argument("--event-slug", default=None,
                   help="sticker_catalog.event_slug filter (e.g. pgl-stockholm-2021)")
    p.add_argument("--names", default=None,
                   help="Comma-separated market_hash_name list (smoke test mode, no catalog required)")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap items processed (smoke test)")
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch + parse but do NOT write to Supabase")
    p.add_argument("--min-delay", type=float, default=5.0,
                   help="Floor between requests in seconds (Steam Market rate limit)")

    viz = sources.add_parser("viz", help="Render Twitter-ready charts from Supabase")
    vz = viz.add_subparsers(dest="cmd", required=True)

    v = vz.add_parser("prize-pool-ladder", help="Top-N tournaments by prize pool")
    v.add_argument("--limit", type=int, default=15)
    v.add_argument("--year", type=int, default=None)
    v.add_argument("--from-source", dest="from_source", default=None, help="liquipedia / pandascore / hltv / manual")
    v.add_argument("--out-dir", default="out")

    v = vz.add_parser("tournament-card", help="Single-tournament hero card")
    v.add_argument("--id", type=int, required=True, help="tournaments.id (local pk)")
    v.add_argument("--out-dir", default="out")

    v = vz.add_parser("sticker-prices", help="Bar chart of current sticker prices")
    v.add_argument("--event-slug", required=True, help="watched_items.event_slug filter")
    v.add_argument("--top", type=int, default=20)
    v.add_argument("--out-dir", default="out")

    liq = sources.add_parser("liquipedia", help="Liquipedia counterstrike wiki")
    lq = liq.add_subparsers(dest="cmd", required=True)
    p = lq.add_parser("run", help="End-to-end pull → Supabase write")
    p.add_argument("--tier-max", type=int, default=2)
    p.add_argument("--months-back", type=int, default=12)
    p.add_argument("--months-forward", type=int, default=6)
    p.add_argument("--limit", type=int, default=100, dest="tournaments_limit")
    p.add_argument("--detail-cap", type=int, default=20, dest="max_tournaments_for_detail")
    p.add_argument("--no-prizes", action="store_true")
    p.add_argument("--no-matches", action="store_true")

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
        if args.cmd == "seed":
            from steam_market.runner import seed_watched_items
            if args.file:
                with open(args.file) as fh:
                    names = [ln for ln in fh.read().splitlines() if ln.strip()]
            else:
                names = [ln for ln in sys.stdin.read().splitlines() if ln.strip()]
            return _print(seed_watched_items(
                names,
                event_slug=args.event_slug,
                category=args.category,
                appid=args.appid,
            ))
        if args.cmd == "refresh":
            from steam_market.runner import run_once
            return _print(run_once(
                event_slug=args.event_slug,
                appid=args.appid,
                currency=args.currency,
                min_delay=args.min_delay,
                proxy=args.proxy,
                max_items=args.max_items,
            ))

    if args.source == "pandascore":
        if args.cmd == "run":
            from pandascore_parser.runner import run_once
            return _print(run_once(
                matches_window_days=args.matches_window_days,
                matches_max_pages=args.matches_max_pages,
                tournaments_max_pages=args.tournaments_max_pages,
                fetch_past_matches=not args.no_past_matches,
                fetch_upcoming_matches=not args.no_upcoming_matches,
                fetch_running_tournaments=not args.no_running_tournaments,
                fetch_upcoming_tournaments=not args.no_upcoming_tournaments,
            ))

    if args.source == "steam-history":
        if args.cmd == "backfill":
            from steam_market.history_runner import backfill
            if bool(args.event_slug) == bool(args.names):
                print("steam-history backfill requires exactly one of "
                      "--event-slug or --names", file=sys.stderr)
                return 2
            names_iter = (
                [n.strip() for n in args.names.split(",") if n.strip()]
                if args.names else None
            )
            return _print(backfill(
                event_slug=args.event_slug,
                names=names_iter,
                dry_run=args.dry_run,
                limit=args.limit,
                min_delay=args.min_delay,
            ))

    if args.source == "viz":
        from pathlib import Path
        out_dir = Path(args.out_dir)
        if args.cmd == "prize-pool-ladder":
            from viz.prize_pool_ladder import render
        elif args.cmd == "tournament-card":
            from viz.tournament_card import render
        elif args.cmd == "sticker-prices":
            from viz.sticker_prices import render
        else:
            print(f"unknown viz command: {args.cmd}", file=sys.stderr)
            return 2
        paths = render(args, out_dir)
        return _print({"ok": True, "paths": [str(p) for p in paths]})

    if args.source == "liquipedia":
        if args.cmd == "run":
            from liquipedia_parser.runner import run_once
            return _print(run_once(
                tier_max=args.tier_max,
                months_back=args.months_back,
                months_forward=args.months_forward,
                tournaments_limit=args.tournaments_limit,
                max_tournaments_for_detail=args.max_tournaments_for_detail,
                fetch_prizes=not args.no_prizes,
                fetch_matches=not args.no_matches,
            ))

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
