#!/usr/bin/env python3
"""Discovery script: dump the live Supabase Postgres schema.

The build spec's "Existing Infrastructure" section is based on a discovery
report that may be inaccurate. Run this against the *real* database and send
the output back BEFORE any pydantic models are written.

Access pattern mirrors the pipeline's Supabase writer: plain httpx against
the PostgREST API with the service_role key. No supabase-py, no direct
Postgres connection (so no DB password is needed).

How it works
------------
1. Fetches the PostgREST OpenAPI document at ``{SUPABASE_URL}/rest/v1/``.
   Its ``definitions`` block lists every table/view in the exposed schema
   together with each column's type and PK/FK notes.
2. For each relation, issues a ``limit=0`` request with ``Prefer: count=exact``
   and reads the exact row count from the ``Content-Range`` response header.

Usage
-----
    pip install httpx python-dotenv

    # credentials via a .env file (looked up next to this script, in
    # backend/, or in the current dir) or via real environment variables:
    export SUPABASE_URL=https://<project-ref>.supabase.co
    export SUPABASE_SERVICE_ROLE=<service_role key, NOT the anon key>

    python backend/scripts/inspect_schema.py          # human-readable report
    python backend/scripts/inspect_schema.py --json    # machine-readable JSON

Note: PostgREST only exposes relations in the configured schema (``public``
by default). Anything in another schema, or hidden from the API, will not
appear here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("missing dependency: run `pip install httpx`")


def _load_env() -> None:
    """Load a .env file if python-dotenv is available. Optional."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    here = Path(__file__).resolve()
    candidates = [
        here.parent / ".env",            # backend/scripts/.env
        here.parents[1] / ".env",        # backend/.env
        Path.cwd() / ".env",             # ./.env
    ]
    for path in candidates:
        if path.is_file():
            load_dotenv(path)
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
        # description form: "...Foreign Key to `public.teams.team_id`.<fk .../>"
        target = ""
        if "`" in description:
            parts = description.split("`")
            if len(parts) >= 2:
                target = parts[1]
        notes.append(f"FK->{target}" if target else "FK")
    return " ".join(notes)


def _row_count(client: httpx.Client, url: str, table: str) -> int | None:
    """Exact row count via the Content-Range header; None if unavailable."""
    try:
        resp = client.get(
            f"{url}/rest/v1/{table}",
            params={"limit": 0},
            headers={"Prefer": "count=exact"},
        )
    except httpx.HTTPError as exc:
        print(f"  ! count failed for {table}: {exc}", file=sys.stderr)
        return None
    if resp.status_code not in (200, 206):
        print(
            f"  ! count failed for {table}: HTTP {resp.status_code}",
            file=sys.stderr,
        )
        return None
    content_range = resp.headers.get("content-range", "")
    total = content_range.rsplit("/", 1)[-1] if "/" in content_range else ""
    return int(total) if total.isdigit() else None


def inspect(url: str, key: str) -> dict:
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    with httpx.Client(headers=headers, timeout=30.0) as client:
        resp = client.get(f"{url}/rest/v1/")
        resp.raise_for_status()
        spec = resp.json()
        definitions = spec.get("definitions", {})

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
                    "row_count": _row_count(client, url, name),
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

    _load_env()
    url, key = _require_env()

    try:
        result = inspect(url, key)
    except httpx.HTTPStatusError as exc:
        return _fail(f"PostgREST returned HTTP {exc.response.status_code}: {exc}")
    except httpx.HTTPError as exc:
        return _fail(f"could not reach Supabase: {exc}")

    if args.json:
        json.dump(result, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_report(result)
    return 0


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
