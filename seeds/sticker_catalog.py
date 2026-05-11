"""Seed sticker_catalog for two CS:GO Majors.

Approach is intentionally data-driven: the hardcoded team + player
rosters generate *candidate* market_hash_names, then a polite probe
against Steam Market ``priceoverview`` filters candidates down to
those that actually exist on Steam. So a typo in a roster (e.g. a
player who moved teams between qualifier and event, an alternate
nickname spelling) simply yields a 'not found' and the row is
skipped — no manual cleanup needed.

Why this matters: Steam Market spellings are picky. "Virtus.Pro" is
the real name on Atlanta 2017 stickers (capital P), not "Virtus.pro".
"device" is the Atlanta 2017 sticker spelling, not "dev1ce". The
priceoverview probe is the ground truth.

Per-event category structure (verified via priceoverview):
  ELEAGUE Atlanta 2017
    team: paper, holo, foil
    autograph: paper, holo, foil, gold
  PGL Stockholm 2021
    team: paper, holo, glitter, foil, gold
    autograph: paper, holo, glitter, gold, champion (Champions Capsule)

The Champion's Capsule uses ``(Champion)`` suffix on Stockholm 2021
autographs of the winning team's 5 players. Atlanta 2017 has no
separate Champion's Capsule — the high-tier autograph is just ``(Foil)``
or ``(Gold)``. The catalog stores each SKU under its real category;
viz layer decides which to chart on the champion-tier panel.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Iterable

import httpx

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Per-event meta
# ----------------------------------------------------------------------------

# (variant_suffix, category-as-stored-in-DB)
TEAM_VARIANTS_ATLANTA = [
    ("",          "paper"),
    (" (Holo)",   "holo"),
    (" (Foil)",   "foil"),
]
PLAYER_VARIANTS_ATLANTA = [
    ("",          "paper"),
    (" (Holo)",   "holo"),
    (" (Foil)",   "foil"),
    (" (Gold)",   "gold"),
]

TEAM_VARIANTS_STOCKHOLM = [
    ("",           "paper"),
    (" (Holo)",    "holo"),
    (" (Glitter)", "glitter"),
    (" (Foil)",    "foil"),
    (" (Gold)",    "gold"),
]
PLAYER_VARIANTS_STOCKHOLM = [
    ("",            "paper"),
    (" (Holo)",     "holo"),
    (" (Glitter)",  "glitter"),
    (" (Gold)",     "gold"),
    (" (Champion)", "champion_gold"),
]


# Roster source: research subagent run 2026-05-11, cross-checked
# against Liquipedia tournament pages. Items marked UNSURE in comments
# below are best-effort and rely on data-driven verification to filter
# any mistakes out at seed time.
ATLANTA_2017 = {
    "event_slug": "eleague-atlanta-2017",
    "event_name_suffix": "Atlanta 2017",
    "team_variants": TEAM_VARIANTS_ATLANTA,
    "player_variants": PLAYER_VARIANTS_ATLANTA,
    "teams": [
        {"team": "Astralis",          "tier": "champion",
         "players": ["device", "dupreeh", "Kjaerbye", "Xyp9x", "gla1ve"]},
        {"team": "Virtus.Pro",        "tier": "finalist",
         "players": ["Snax", "NEO", "TaZ", "pashaBiceps", "byali"]},
        {"team": "Fnatic",            "tier": "top3",
         "players": ["olofmeister", "KRIMZ", "dennis", "wenton", "twist"]},
        {"team": "SK Gaming",         "tier": "rest",
         "players": ["FalleN", "fer", "coldzera", "TACO", "fnx"]},
        {"team": "Natus Vincere",     "tier": "rest",
         "players": ["s1mple", "flamie", "GuardiaN", "Edward", "seized"]},
        {"team": "North",             "tier": "rest",
         "players": ["MSL", "cajunb", "k0nfig", "Magisk", "aizy"]},
        {"team": "Gambit",            "tier": "rest",
         "players": ["AdreN", "Dosia", "mou", "HObbit", "Zeus"]},
        {"team": "FlipSid3 Tactics",  "tier": "rest",
         "players": ["WorldEdit", "B1ad3", "markeloff", "DavCost", "blocker"]},
        {"team": "Team Liquid",       "tier": "rest",
         "players": ["nitr0", "EliGE", "Hiko", "jdm64", "adreN"]},
        {"team": "G2 Esports",        "tier": "rest",
         "players": ["shox", "SmithZz", "ScreaM", "bodyy", "RpK"]},
        {"team": "mousesports",       "tier": "rest",
         "players": ["NiKo", "chrisJ", "denis", "loWel", "nex"]},
        {"team": "OpTic Gaming",      "tier": "rest",
         "players": ["stanislaw", "RUSH", "mixwell", "NAF", "shahzaM"]},
        {"team": "HellRaisers",       "tier": "rest",
         "players": ["ANGE1", "DeadFox", "bondik", "oskar", "kUcheR"]},
        {"team": "Heroic",            "tier": "rest",
         "players": ["Snappi", "rdl", "Pimp", "nartOuT", "Friis"]},
        {"team": "GODSENT",           "tier": "rest",
         "players": ["pronax", "schneider", "znajder", "flusha", "JW"]},
        {"team": "Cloud9",            "tier": "rest",
         "players": ["n0thing", "shroud", "Stewie2K", "autimatic", "Skadoodle"]},
    ],
}

STOCKHOLM_2021 = {
    "event_slug": "pgl-stockholm-2021",
    "event_name_suffix": "Stockholm 2021",
    "team_variants": TEAM_VARIANTS_STOCKHOLM,
    "player_variants": PLAYER_VARIANTS_STOCKHOLM,
    "teams": [
        {"team": "Natus Vincere",      "tier": "champion",
         "players": ["s1mple", "electronic", "Perfecto", "Boombl4", "b1t"]},
        {"team": "G2 Esports",         "tier": "finalist",
         "players": ["NiKo", "huNter-", "AmaNEk", "nexa", "JACKZ"]},
        {"team": "Heroic",             "tier": "top3",
         "players": ["cadiaN", "stavn", "TeSeS", "refrezh", "sjuush"]},
        {"team": "Ninjas in Pyjamas",  "tier": "rest",
         "players": ["dev1ce", "REZ", "Plopski", "hampus", "es3tag"]},
        {"team": "Gambit",             "tier": "rest",
         "players": ["Hobbit", "Ax1Le", "interz", "sh1ro", "nafany"]},
        {"team": "FaZe Clan",          "tier": "rest",
         "players": ["karrigan", "rain", "Twistzz", "broky", "ropz"]},
        {"team": "Virtus.pro",         "tier": "rest",  # lowercase p on Stockholm capsule
         "players": ["Jame", "buster", "YEKINDAR", "qikert", "FL1T"]},
        {"team": "Vitality",           "tier": "rest",
         "players": ["ZywOo", "apEX", "shox", "Kyojin", "misutaaa"]},
        {"team": "Astralis",           "tier": "rest",
         "players": ["gla1ve", "Xyp9x", "Magisk", "blameF", "k0nfig"]},
        {"team": "ENCE",               "tier": "rest",
         "players": ["Aleksib", "Snappi", "allu", "sergej", "Aerial"]},
        {"team": "Team Liquid",        "tier": "rest",
         "players": ["EliGE", "NAF", "Stewie2K", "FalleN", "oSee"]},
        {"team": "Team Spirit",        "tier": "rest",
         "players": ["chopper", "magixx", "degster", "sdy", "Patsi"]},
        {"team": "MOUZ",               "tier": "rest",
         "players": ["frozen", "Bymas", "acoR", "torzsi", "dexter"]},
        {"team": "Copenhagen Flames",  "tier": "rest",
         "players": ["roeJ", "HooXi", "Nodios", "birdfromsky", "fox"]},
        {"team": "FURIA",              "tier": "rest",
         "players": ["arT", "yuurih", "KSCERATO", "VINI", "saffee"]},
        {"team": "BIG",                "tier": "rest",
         "players": ["tabseN", "tiziaN", "syrsoN", "XANTARES", "k1to"]},
        {"team": "Evil Geniuses",      "tier": "rest",
         "players": ["CeRq", "Brehze", "tarik", "MICHU", "oBo"]},
        {"team": "paiN Gaming",        "tier": "rest",
         "players": ["hardzao", "kaiko", "biguzera", "land1n", "prt"]},
        {"team": "Renegades",          "tier": "rest",
         "players": ["Liazz", "jks", "INS", "AZR", "Sico"]},
        {"team": "Entropiq",           "tier": "rest",
         "players": ["Forester", "Lack1", "shokkk", "HUNDEN", "kadji"]},
        {"team": "Movistar Riders",    "tier": "rest",
         "players": ["mopoz", "DeathZz", "alex", "dav1g", "Mixwell"]},
        {"team": "Sharks",             "tier": "rest",
         "players": ["leo_drk", "THRZ", "jnt", "exit", "rikz"]},
        {"team": "GODSENT",            "tier": "rest",
         "players": ["latto", "TMB", "kRaSnaL", "MAJ3R", "XELLOW"]},
        {"team": "Bad News Bears",     "tier": "rest",
         "players": ["Swisher", "JT", "Sonic", "FaNg", "PwnAlone"]},
    ],
}

EVENTS: dict[str, dict] = {
    ATLANTA_2017["event_slug"]: ATLANTA_2017,
    STOCKHOLM_2021["event_slug"]: STOCKHOLM_2021,
}


# ----------------------------------------------------------------------------
# Candidate generation
# ----------------------------------------------------------------------------

@dataclass
class Candidate:
    market_hash_name: str
    event_slug: str
    category: str
    team_name: str
    player_name: str | None
    placement_tier: str   # 'top3' | 'rest' | 'champion' | 'finalist'


def _team_placement_tier(team_tier: str) -> str:
    """Map roster tier → placement_tier as stored in sticker_catalog.

    For TEAM-level stickers we collapse {champion, finalist, top3} into
    'top3' — that's the "podium teams" bucket used by viz.top3-vs-rest
    line styling. Champion / finalist tiers only apply to autographs.
    """
    if team_tier in ("champion", "finalist", "top3"):
        return "top3"
    return "rest"


def generate_candidates(event: dict) -> list[Candidate]:
    """Expand the event dict into every (team or player, variant) candidate."""
    suffix = event["event_name_suffix"]
    out: list[Candidate] = []

    for team in event["teams"]:
        team_name = team["team"]
        roster_tier = team["tier"]

        # Team-level stickers
        for variant_suffix, category in event["team_variants"]:
            out.append(Candidate(
                market_hash_name=f"Sticker | {team_name}{variant_suffix} | {suffix}",
                event_slug=event["event_slug"],
                category=category,
                team_name=team_name,
                player_name=None,
                placement_tier=_team_placement_tier(roster_tier),
            ))

        # Player-level autograph stickers. To keep probe traffic reasonable
        # we only enumerate players for top3+finalist+champion teams here.
        # Rest-team autographs can be added later by expanding this filter
        # — the schema doesn't change.
        if roster_tier in ("champion", "finalist", "top3"):
            for player_name in team["players"]:
                for variant_suffix, category in event["player_variants"]:
                    # Champion category only applies to the actual champion
                    # team's autographs.
                    if category == "champion_gold" and roster_tier != "champion":
                        continue
                    pl_tier = (
                        "champion" if roster_tier == "champion"
                        else "finalist" if roster_tier == "finalist"
                        else "top3"
                    )
                    out.append(Candidate(
                        market_hash_name=f"Sticker | {player_name}{variant_suffix} | {suffix}",
                        event_slug=event["event_slug"],
                        category=category,
                        team_name=team_name,
                        player_name=player_name,
                        placement_tier=pl_tier,
                    ))
    return out


# ----------------------------------------------------------------------------
# Verification probe + upsert
# ----------------------------------------------------------------------------

_PRICEOVERVIEW = "https://steamcommunity.com/market/priceoverview/"


def _exists_on_steam(market_hash_name: str, client: httpx.Client) -> bool:
    """Probe Steam Market priceoverview. ``success=True`` means the SKU exists.

    No-recent-sales items also return success=True (they simply have no
    ``lowest_price`` key). That's still a valid catalog entry — the
    sticker exists, it just hasn't traded in 24h.
    """
    r = client.get(_PRICEOVERVIEW, params={
        "appid": 730,
        "market_hash_name": market_hash_name,
        "currency": 1,   # USD
    }, timeout=20)
    if r.status_code in (429, 502, 503):
        # Back off and retry once. priceoverview rate-limits aggressively.
        time.sleep(15)
        r = client.get(_PRICEOVERVIEW, params={
            "appid": 730,
            "market_hash_name": market_hash_name,
            "currency": 1,
        }, timeout=20)
    if r.status_code != 200:
        log.warning("priceoverview status=%s for %r", r.status_code, market_hash_name)
        return False
    return bool(r.json().get("success"))


def _sb_headers() -> dict:
    key = os.environ["SUPABASE_SERVICE_ROLE"]
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def _upsert(rows: list[dict]) -> int:
    if not rows:
        return 0
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    r = httpx.post(
        f"{base}/sticker_catalog?on_conflict=market_hash_name",
        json=rows, headers=_sb_headers(), timeout=60,
    )
    if r.status_code not in (200, 201, 204):
        log.error("upsert failed: %s %s", r.status_code, r.text[:300])
        r.raise_for_status()
    return len(rows)


def seed(
    event_slug: str,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    throttle: float = 3.0,
) -> dict:
    """Verify candidates against Steam, write surviving ones to sticker_catalog."""
    if event_slug not in EVENTS:
        raise ValueError(
            f"unknown event_slug={event_slug!r}; known: {list(EVENTS)}"
        )
    event = EVENTS[event_slug]
    candidates = generate_candidates(event)
    if limit:
        candidates = candidates[:limit]

    log.info("event=%s candidates=%d throttle=%.1fs (~%.1f min)",
             event_slug, len(candidates), throttle, len(candidates) * throttle / 60)

    rows_to_write: list[dict] = []
    rejected: list[str] = []
    started = time.monotonic()

    with httpx.Client(headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
                      "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
    }) as client:
        for i, c in enumerate(candidates, 1):
            time.sleep(throttle)
            try:
                ok = _exists_on_steam(c.market_hash_name, client)
            except Exception as exc:
                log.warning("[%d/%d] probe exception for %r: %s",
                            i, len(candidates), c.market_hash_name, exc)
                rejected.append(c.market_hash_name)
                continue
            if not ok:
                rejected.append(c.market_hash_name)
                if i % 10 == 0:
                    log.info("[%d/%d] %d kept, %d rejected so far",
                             i, len(candidates), len(rows_to_write), len(rejected))
                continue
            rows_to_write.append({
                "market_hash_name": c.market_hash_name,
                "event_slug":       c.event_slug,
                "category":         c.category,
                "team_name":        c.team_name,
                "player_name":      c.player_name,
                "placement_tier":   c.placement_tier,
            })

    written = 0 if dry_run else _upsert(rows_to_write)
    elapsed = time.monotonic() - started

    return {
        "event_slug": event_slug,
        "candidates_probed": len(candidates),
        "candidates_kept": len(rows_to_write),
        "candidates_rejected": len(rejected),
        "rows_written": written,
        "dry_run": dry_run,
        "elapsed_sec": round(elapsed, 1),
        # Sample first 10 rejects so a user can spot roster typos in the log.
        "rejected_sample": rejected[:10],
    }
