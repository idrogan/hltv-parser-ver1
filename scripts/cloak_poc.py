"""CloakBrowser proof-of-concept — does it pierce the Cloudflare WAF on
HLTV and EsportsCharts?

This is a SPIKE, deliberately isolated from the production pipeline:

  * It is NOT imported by app.py / cli.py / any runner.
  * It does NOT write to Supabase.
  * It does NOT touch the working curl_cffi scrapers.
  * `cloakbrowser` is intentionally NOT added to requirements.txt — it
    pulls a stealth Chromium binary (hundreds of MB) we don't want in
    the API image. Install it only where you run this spike.

Why CloakBrowser-only, no Crawlee yet
-------------------------------------
The user asked about CloakBrowser + Crawlee. Crawlee is a queue /
retry / concurrency orchestration layer; CloakBrowser is the stealth
browser that actually has to defeat the WAF. There is no point wrapping
Crawlee around a fetch that doesn't yet bypass Cloudflare. So this spike
answers the load-bearing question first — "does CloakBrowser get a real
page back?" — on its documented async API (`launch_async`). Once this
passes 3x in a row (the bar set by docs/RESUMING_HLTV.md), wiring
Crawlee's PlaywrightCrawler on top is mechanical.

Install + run
-------------
    pip install cloakbrowser          # first run also downloads Chromium
    python scripts/cloak_poc.py                 # one round, both targets
    python scripts/cloak_poc.py --rounds 3      # the resume-checklist bar
    python scripts/cloak_poc.py --target hltv --headed

Exit code is 0 only if every target passed in every round — so this is
CI/cron-friendly if you ever want an automated gate.

Interpreting output
-------------------
Per target we classify the returned HTML into:
  * PASS    — looks like the real page (expected markers present, no
              challenge markers).
  * BLOCKED — Cloudflare challenge / "Just a moment" / Turnstile still
              showing. CloakBrowser did not pierce it.
  * UNSURE  — 200-ish content but neither expected markers nor a clear
              challenge. Eyeball the saved HTML dump.

Each fetch's HTML is dumped to out/cloak_poc/<target>-<round>.html so
you can inspect what actually came back.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path


# Targets chosen to mirror the two unstable endpoints called out in
# docs/RESUMING_HLTV.md (HLTV /team) and the EsC leaderboard the
# existing escharts_parser reads.
TARGETS = {
    "hltv": {
        "url": "https://www.hltv.org/team/4608/natus-vincere",
        # Substrings that only appear on the real, rendered team page.
        "expect_any": ["Natus Vincere", "navi", "Maps played", "core-overview"],
    },
    "escharts": {
        "url": "https://escharts.com/tournaments/cs2",
        "expect_any": ["tournament", "viewers", "Peak", "hours watched"],
    },
}

# Markers that mean Cloudflare (or another WAF) is still in the way.
_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-challenge",
    "challenge-platform",
    "cf_chl_opt",
    "attention required",
    "turnstile",
    "checking your browser",
    "_cf_chl",
    "enable javascript and cookies to continue",
)


def _classify(html: str, expect_any: list[str]) -> str:
    low = html.lower()
    if any(m in low for m in _CHALLENGE_MARKERS):
        return "BLOCKED"
    if any(s.lower() in low for s in expect_any):
        return "PASS"
    return "UNSURE"


async def _fetch_one(launch_async, url: str, *, headed: bool,
                     settle_s: float, nav_timeout_ms: int) -> tuple[int, str]:
    """Return (http_status_or_-1, html). One isolated browser per fetch
    so a poisoned context can't leak into the next target."""
    # CloakBrowser mirrors Playwright's API. headless is the default;
    # `headed` is occasionally useful when a challenge needs a visible
    # paint loop to auto-solve.
    browser = await launch_async(headless=not headed)
    try:
        page = await browser.new_page()
        resp = await page.goto(url, timeout=nav_timeout_ms, wait_until="domcontentloaded")
        # Give Turnstile / JS challenge a moment to auto-resolve and the
        # real DOM to swap in before we snapshot.
        await page.wait_for_timeout(int(settle_s * 1000))
        html = await page.content()
        status = resp.status if resp is not None else -1
        return status, html
    finally:
        await browser.close()


async def _run(args) -> int:
    try:
        from cloakbrowser import launch_async
    except ImportError:
        print(
            "cloakbrowser is not installed in this environment.\n"
            "  pip install cloakbrowser\n"
            "(first launch also downloads the stealth Chromium binary.)",
            file=sys.stderr,
        )
        return 2

    selected = (
        {args.target: TARGETS[args.target]} if args.target else TARGETS
    )
    dump_dir = Path("out/cloak_poc")
    dump_dir.mkdir(parents=True, exist_ok=True)

    # results[target] = list of verdicts, one per round
    results: dict[str, list[str]] = {name: [] for name in selected}

    for rnd in range(1, args.rounds + 1):
        for name, spec in selected.items():
            t0 = time.monotonic()
            try:
                status, html = await _fetch_one(
                    launch_async, spec["url"],
                    headed=args.headed,
                    settle_s=args.settle,
                    nav_timeout_ms=args.timeout * 1000,
                )
                verdict = _classify(html, spec["expect_any"])
                (dump_dir / f"{name}-r{rnd}.html").write_text(html, errors="replace")
            except Exception as exc:  # network, nav timeout, browser crash
                status, verdict = -1, "ERROR"
                html = f"{type(exc).__name__}: {exc}"
            dt = time.monotonic() - t0
            results[name].append(verdict)
            print(
                f"round {rnd}  {name:9} http={status:<4} "
                f"{verdict:8} {dt:5.1f}s  bytes={len(html)}"
            )
            if args.delay and not (rnd == args.rounds and name == list(selected)[-1]):
                await asyncio.sleep(args.delay)

    # Summary — the resume bar is "PASS in every round".
    print("\n=== summary ===")
    all_green = True
    for name, verdicts in results.items():
        passes = verdicts.count("PASS")
        ok = passes == len(verdicts)
        all_green = all_green and ok
        print(f"  {name:9} {passes}/{len(verdicts)} PASS  {verdicts}")
    print(f"\nHTML dumps: {dump_dir}/")
    if all_green:
        print("RESULT: all targets passed every round — CloakBrowser pierces the WAF here.")
        return 0
    print("RESULT: not all green — inspect the dumps before trusting this stack.")
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description="CloakBrowser WAF-bypass spike")
    p.add_argument("--target", choices=sorted(TARGETS),
                   help="Only test one target (default: all)")
    p.add_argument("--rounds", type=int, default=1,
                   help="Repeat N times; resume checklist wants 3 consecutive PASS")
    p.add_argument("--headed", action="store_true",
                   help="Run with a visible browser window")
    p.add_argument("--settle", type=float, default=6.0,
                   help="Seconds to wait after navigation for challenge auto-solve")
    p.add_argument("--timeout", type=int, default=60,
                   help="Per-navigation timeout in seconds")
    p.add_argument("--delay", type=float, default=8.0,
                   help="Seconds between fetches (be polite to the WAF)")
    return asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
