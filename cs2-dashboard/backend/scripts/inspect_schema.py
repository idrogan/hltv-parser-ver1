#!/usr/bin/env python3
"""Discovery script: dump the live Supabase Postgres schema.

Standard-library only — no pip installs, no virtualenv, no project layout
needed. Copy this single file anywhere with Python 3.8+ and outbound
internet to your Supabase project and run it.

The build spec's "Existing Infrastructure" section is based on a discovery
report that may be inaccurate. Run this against the *real* database and send
the output back BEFORE any pydantic models are written.

Access pattern: plain HTTPS against the PostgREST API with the service_role
key. No supabase-py, no direct Postgres connection (so no DB password).

How it works
------------
1. Fetches the PostgREST OpenAPI document at ``{SUPABASE_URL}/rest/v1/``.
   Its ``definitions`` block lists every table/view in the exposed schema
   together with each column's type and PK/FK notes.
2. For each relation, issues a ``limit=0`` request with ``Prefer: count=exact``
   and reads the exact row count from the ``Content-Range`` response header.

Usage
-----
    # credentials via real environment variables or a nearby .env file
    export SUPABASE_URL=https://<project-ref>.supabase.co
    export SUPABASE_SERVICE_ROLE=<service_role key, NOT the anon key>

    python3 inspect_schema.py          # human-readable report
    python3 inspect_schema.py --json    # machine-readable JSON

Note: PostgREST only exposes relations in the configured schema (``public``
by default). Anything in another schema, or hidden from the API, will not
appear here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _load_env_file() -> None:
    """Best-effort load of KEY=VALUE lines from a nearby .env file (no deps)."""
    here = Path(__file__).resolve()
    candidates = [here.parent / ".env", Path.cwd() / ".env"]
    if len(here.parents) > 1:
        candidates.insert(1, here.parents[1] / ".env")
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        return


def _require_env() -> tuple[str, str]:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE", "").strip()
    missing = [
        name
        for name, val in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_ROLE", key))
        if not val
    ]
    if missing:
        sys.exit(f"missing required env var(s): {', '.join(missing)}")
    return url, key


def _auth_headers(key: str) -> dict:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }


def _request(url: str, headers: dict) -> tuple[int, object, bytes]:
    """GET a URL. Returns (status, headers, body). HTTPError is unwrapped;
    network failures (URLError) propagate to the caller."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def _fmt_type(prop: dict) -> str:
    """Render a column type from an OpenAPI property definition."""
    # PostgREST puts the real Postgres type in `format`; `type` is the JSON type.
    return prop.get("format") or prop.get("type") or "unknown"


def _key_note(description: str) -> str:
    """Extract a short PK/FK marker from a PostgREST column description."""
    notes = []
    if "<pk/>" in description or "Primary Key" in description:
        notes.append("PK")
    if "<fk " in description or "Foreign Key" in description:
        # form: "...Foreign Key to `public.teams.team_id`.<fk .../>"
        target = description.split("`")[1] if "`" in description else ""
        notes.append(f"FK->{target}" if target else "FK")
    return " ".join(notes)


def _row_count(url: str, key: str, table: str) -> int | None:
    """Exact row count via the Content-Range header; None if unavailable."""
    headers = _auth_headers(key)
    headers["Prefer"] = "count=exact"
    full = f"{url}/rest/v1/{urllib.parse.quote(table)}?limit=0"
    try:
        status, resp_headers, _ = _request(full, headers)
    except urllib.error.URLError as exc:
        print(f"  ! count failed for {table}: {exc}", file=sys.stderr)
        return None
    if status not in (200, 206):
        print(f"  ! count failed for {table}: HTTP {status}", file=sys.stderr)
        return None
    content_range = resp_headers.get("Content-Range", "") or ""
    tail = content_range.rsplit("/", 1)[-1] if "/" in content_range else ""
    return int(tail) if tail.isdigit() else None


def inspect(url: str, key: str) -> dict:
    status, _, body = _request(f"{url}/rest/v1/", _auth_headers(key))
    if status != 200:
        snippet = body[:300].decode("utf-8", "replace")
        sys.exit(f"PostgREST root returned HTTP {status}: {snippet}")
    definitions = json.loads(body).get("definitions", {})

    relations: list[dict] = []
    for name in sorted(definitions):
        props: dict = definitions[name].get("properties", {})
        columns = [
            {
                "name": col,
                "type": _fmt_type(meta),
                "keys": _key_note(meta.get("description", "")),
            }
            for col, meta in props.items()
        ]
        relations.append(
            {
                "name": name,
                "row_count": _row_count(url, key, name),
                "columns": columns,
            }
        )
    return {"supabase_url": url, "relation_count": len(relations), "relations": relations}


def _print_report(result: dict) -> None:
    print(f"Supabase: {result['supabase_url']}")
    print(f"Relations exposed via PostgREST: {result['relation_count']}")
    print("=" * 72)
    for rel in result["relations"]:
        count = rel["row_count"]
        count_str = f"{count:,} rows" if count is not None else "row count: n/a"
        print(f"\n{rel['name']}  ({count_str})")
        for col in rel["columns"]:
            keys = f"  [{col['keys']}]" if col["keys"] else ""
            print(f"    - {col['name']}: {col['type']}{keys}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dump the live Supabase schema.")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    _load_env_file()
    url, key = _require_env()

    try:
        result = inspect(url, key)
    except urllib.error.URLError as exc:
        print(f"error: could not reach Supabase: {exc}", file=sys.stderr)
        return 1

    if args.json:
        json.dump(result, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
