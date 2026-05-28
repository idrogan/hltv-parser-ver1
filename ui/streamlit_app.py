"""Streamlit UI over the esports-data parsers.

Runs the service layer in-process (not via the HTTP API), so it can pick
the CloakBrowser backend that actually pierces Cloudflare for HLTV /
EsportsCharts, and it skips the API token entirely.

Run it:
    pip install -r requirements-ui.txt
    streamlit run ui/streamlit_app.py

Cloak note: the cloak backend launches a stealth Chromium and was
validated from a residential IP; a datacenter egress may still get
challenged. Steam and Liquipedia use their own (non-cloak) clients.
"""
from __future__ import annotations

import os

# Local tool: the operator explicitly wants HLTV, so lift the pause gate
# that protects the public API deployment.
os.environ.setdefault("HLTV_ENABLED", "true")

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Esports Data", layout="wide")


# --------------------------------------------------------------------------
# Clients (cached so the cloak Chromium is reused across Streamlit reruns)
# --------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _cloak(base_url: str, dump_env: str, min_delay: float):
    from hltv_parser.cloak_client import CloakClient

    return CloakClient(
        min_delay=max(min_delay, 4.0),
        base_url=base_url,
        dump_env=dump_env,
        headless=True,
    )


def hltv_service(backend: str, min_delay: float, proxy: str | None):
    from hltv_parser.service import HLTVService

    if backend == "cloak":
        client = _cloak("https://www.hltv.org", "HLTV_CLOAK_DUMP_DIR", min_delay)
    else:
        from hltv_parser.client import HLTVClient

        client = HLTVClient(min_delay=min_delay, proxy=proxy or None)
    return HLTVService(client)


def escharts_service(backend: str, min_delay: float, proxy: str | None):
    from escharts_parser.service import EsChartsService

    if backend == "cloak":
        client = _cloak("https://escharts.com", "ESCHARTS_CLOAK_DUMP_DIR", min_delay)
    else:
        from escharts_parser.client import EsChartsClient

        client = EsChartsClient(min_delay=min_delay, proxy=proxy or None)
    return EsChartsService(client)


def steam_service(min_delay: float, proxy: str | None, login_secure: str | None):
    from steam_market.client import SteamClient
    from steam_market.service import SteamMarketService

    return SteamMarketService(
        SteamClient(min_delay=min_delay, proxy=proxy or None,
                    login_secure_cookie=login_secure or None)
    )


def liquipedia_service(min_delay: float):
    from liquipedia_parser.service import LiquipediaService
    from liquipedia_parser.client import LiquipediaClient

    return LiquipediaService(LiquipediaClient(min_delay=min_delay))


# --------------------------------------------------------------------------
# Result rendering
# --------------------------------------------------------------------------
def _table(rows: list, key: str, label: str | None = None) -> None:
    if label:
        st.markdown(f"**{label}** ({len(rows)})")
    if rows and isinstance(rows[0], dict):
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)
        st.download_button(
            "Download CSV", df.to_csv(index=False).encode("utf-8"),
            f"{key}.csv", "text/csv", key=f"dl_{key}",
        )
    else:
        st.write(rows)


def render(data) -> None:
    if isinstance(data, list):
        _table(data, "result")
    elif isinstance(data, dict):
        scalars = {k: v for k, v in data.items() if not isinstance(v, list)}
        lists = {k: v for k, v in data.items() if isinstance(v, list)}
        if scalars:
            st.json(scalars)
        for k, v in lists.items():
            _table(v, k, label=k)
    else:
        st.write(data)
    with st.expander("Raw JSON"):
        st.json(data)


def run(fn, *args, **kwargs) -> None:
    """Execute a service call with a spinner and friendly error surface."""
    try:
        with st.spinner("Fetching…"):
            data = fn(*args, **kwargs)
        render(data)
    except Exception as exc:  # noqa: BLE001 - surface any scraper error in the UI
        st.error(f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# Sidebar — transport
# --------------------------------------------------------------------------
st.sidebar.header("Transport")
backend = st.sidebar.radio(
    "Backend (HLTV / EsportsCharts)", ["cloak", "curl"],
    help="cloak = stealth Chromium that pierces Cloudflare (needed for HLTV/EsC). "
         "curl = fast but WAF-blocked on those sites.",
)
min_delay = st.sidebar.number_input("Min delay (s)", 0.0, 30.0, 4.0, 0.5)
proxy = st.sidebar.text_input("Proxy URL", "", help="http://user:pass@host:port")
login_secure = st.sidebar.text_input(
    "Steam steamLoginSecure cookie", "", type="password",
    help="Only needed for Steam price history.",
)
if backend == "cloak":
    st.sidebar.caption("First cloak call launches Chromium (~10s) and is reused after.")

st.title("Esports Data")
source = st.radio(
    "Source", ["HLTV", "EsportsCharts", "Steam Market", "Liquipedia", "PandaScore (ETL)"],
    horizontal=True,
)


# --------------------------------------------------------------------------
# HLTV
# --------------------------------------------------------------------------
if source == "HLTV":
    op = st.selectbox(
        "Query",
        ["Team overview", "Team maps", "Team matches", "Player",
         "Search team", "Rankings", "Upcoming matches", "Results"],
    )
    svc = lambda: hltv_service(backend, min_delay, proxy)

    if op in ("Team overview", "Team maps", "Team matches"):
        c1, c2, c3 = st.columns(3)
        team_id = c1.number_input("Team ID", min_value=1, value=4914, step=1)
        slug = c2.text_input("Slug", "3dmax")
        months_back = c3.number_input("Months back", 1, 60, 3)
        with_sides = (
            st.checkbox("With CT/T sides (one extra fetch per map)")
            if op == "Team maps" else False
        )
        if st.button("Run", type="primary"):
            if op == "Team overview":
                run(svc().team_overview, int(team_id), slug, months_back=int(months_back))
            elif op == "Team maps":
                run(svc().team_map_stats, int(team_id), slug,
                    months_back=int(months_back), with_sides=with_sides)
            else:
                run(svc().team_matches, int(team_id), slug, months_back=int(months_back))

    elif op == "Player":
        c1, c2, c3 = st.columns(3)
        player_id = c1.number_input("Player ID", min_value=1, value=7998, step=1)
        slug = c2.text_input("Slug", "s1mple")
        months_back = c3.number_input("Months back", 1, 60, 3)
        if st.button("Run", type="primary"):
            run(svc().player_stats, int(player_id), slug, months_back=int(months_back))

    elif op == "Search team":
        name = st.text_input("Team name", "3dmax")
        if st.button("Run", type="primary"):
            run(lambda: {"results": svc().find_team(name)})

    elif op == "Rankings":
        if st.button("Run", type="primary"):
            run(lambda: {"rankings": svc().rankings()})

    elif op == "Upcoming matches":
        if st.button("Run", type="primary"):
            run(lambda: {"matches": svc().upcoming_matches()})

    elif op == "Results":
        offset = st.number_input("Offset", 0, 10000, 0, 100)
        if st.button("Run", type="primary"):
            run(lambda: {"results": svc().results(offset=int(offset))})


# --------------------------------------------------------------------------
# EsportsCharts
# --------------------------------------------------------------------------
elif source == "EsportsCharts":
    op = st.selectbox("Query", ["Tournaments (list)", "Tournament (detail)"])
    svc = lambda: escharts_service(backend, min_delay, proxy)

    if op == "Tournaments (list)":
        c1, c2 = st.columns(2)
        game = c1.text_input("Game slug", "csgo",
                             help="Counter-Strike (incl. CS2) = csgo. Also dota2, lol, valorant…")
        year = c2.number_input("Year (0 = current)", 0, 2100, 0)
        if st.button("Run", type="primary"):
            run(lambda: {"tournaments": svc().tournaments(
                game=game, year=int(year) or None)})

    else:
        c1, c2 = st.columns(2)
        game = c1.text_input("Game slug", "csgo")
        slug = c2.text_input("Tournament slug", "intel-extreme-masters-atlanta-2026")
        if st.button("Run", type="primary"):
            run(svc().tournament, game, slug)


# --------------------------------------------------------------------------
# Steam Market
# --------------------------------------------------------------------------
elif source == "Steam Market":
    op = st.selectbox("Query", ["Price", "Search", "Price history"])
    svc = lambda: steam_service(min_delay, proxy, login_secure)

    if op == "Price":
        name = st.text_input("Market hash name", "AK-47 | Redline (Field-Tested)")
        c1, c2 = st.columns(2)
        appid = c1.number_input("App ID", 1, 999999, 730)
        currency = c2.number_input("Currency (1=USD)", 1, 50, 1)
        if st.button("Run", type="primary"):
            run(svc().price_overview, name, appid=int(appid), currency=int(currency))

    elif op == "Search":
        query = st.text_input("Query", "Katowice 2014 Holo")
        c1, c2, c3 = st.columns(3)
        appid = c1.number_input("App ID", 1, 999999, 730)
        count = c2.number_input("Count", 1, 100, 20)
        start = c3.number_input("Start", 0, 10000, 0)
        if st.button("Run", type="primary"):
            run(svc().search, query, appid=int(appid), count=int(count), start=int(start))

    else:
        name = st.text_input("Market hash name", "AK-47 | Redline (Field-Tested)")
        appid = st.number_input("App ID", 1, 999999, 730)
        st.caption("Price history needs the steamLoginSecure cookie (sidebar).")
        if st.button("Run", type="primary"):
            run(svc().price_history, name, appid=int(appid))


# --------------------------------------------------------------------------
# Liquipedia
# --------------------------------------------------------------------------
elif source == "Liquipedia":
    op = st.selectbox("Query", ["Recent tournaments", "Prize distribution", "Match results"])
    svc = lambda: liquipedia_service(min_delay)

    if op == "Recent tournaments":
        c1, c2, c3, c4 = st.columns(4)
        tier_max = c1.number_input("Tier max", 1, 5, 2)
        months_back = c2.number_input("Months back", 0, 60, 12)
        months_forward = c3.number_input("Months fwd", 0, 60, 6)
        limit = c4.number_input("Limit", 1, 500, 100)
        if st.button("Run", type="primary"):
            run(lambda: {"tournaments": svc().recent_tournaments(
                tier_max=int(tier_max), months_back=int(months_back),
                months_forward=int(months_forward), limit=int(limit))})

    elif op == "Prize distribution":
        page = st.text_input("Page name", "BLAST/Major/2025")
        if st.button("Run", type="primary"):
            run(lambda: {"prizes": svc().prize_distribution(page)})

    else:
        page = st.text_input("Page name", "BLAST/Major/2025")
        limit = st.number_input("Limit", 1, 1000, 200)
        if st.button("Run", type="primary"):
            run(lambda: {"matches": svc().match_results(page, limit=int(limit))})


# --------------------------------------------------------------------------
# PandaScore (ETL runner — writes to Supabase)
# --------------------------------------------------------------------------
elif source == "PandaScore (ETL)":
    st.warning(
        "This runs the end-to-end PandaScore → Supabase ETL. It needs "
        "PANDASCORE_TOKEN and Supabase credentials in the environment, and "
        "writes rows rather than just returning them."
    )
    c1, c2, c3 = st.columns(3)
    window = c1.number_input("Matches window (days)", 1, 60, 7)
    m_pages = c2.number_input("Match pages", 1, 50, 6)
    t_pages = c3.number_input("Tournament pages", 1, 50, 4)
    c4, c5 = st.columns(2)
    past = c4.checkbox("Past matches", True)
    upcoming = c4.checkbox("Upcoming matches", True)
    running_t = c5.checkbox("Running tournaments", True)
    upcoming_t = c5.checkbox("Upcoming tournaments", True)
    if st.button("Run ETL", type="primary"):
        from pandascore_parser.runner import run_once

        run(run_once, matches_window_days=int(window), matches_max_pages=int(m_pages),
            tournaments_max_pages=int(t_pages), fetch_past_matches=past,
            fetch_upcoming_matches=upcoming, fetch_running_tournaments=running_t,
            fetch_upcoming_tournaments=upcoming_t)
