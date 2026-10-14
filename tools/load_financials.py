"""Pull key figures from registries that expose them (ANAF, Brønnøysund) and
upsert them into Supabase ``financial_fact``.

Runs inside the parser container, which already has SUPABASE_* in its env:

    docker exec esports-data python tools/load_financials.py                 # all monitored entities
    docker exec esports-data python tools/load_financials.py --entity ro_anaf:16896480 --from-year 2014
    docker exec esports-data python tools/load_financials.py --dry-run       # print rows, write nothing

Figures from official registry APIs are stored as reliability 'A',
extracted_by 'registry_api', verified = true. The registry label and the
exact source URL go into ``notes`` so every number stays traceable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from registries.client import RegistryError  # noqa: E402
from registries.service import RegistryService  # noqa: E402

FIGURE_REGISTRIES = ("ro_anaf", "no_brreg")


class Supabase:
    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/rest/v1"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _req(self, method: str, path: str, body=None, extra_headers=None):
        req = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={**self.headers, **(extra_headers or {})},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def entities(self, registries: tuple[str, ...]) -> list[dict]:
        q = urllib.parse.urlencode({
            "select": "id,registry,entity_id,name",
            "monitor": "eq.true",
            "registry": f"in.({','.join(registries)})",
        })
        return self._req("GET", f"/legal_entity?{q}")

    def upsert_facts(self, rows: list[dict]) -> None:
        for i in range(0, len(rows), 500):
            self._req(
                "POST",
                "/financial_fact?on_conflict=legal_entity_id,period_end,metric,segment",
                rows[i:i + 500],
                {"Prefer": "resolution=merge-duplicates,return=minimal"},
            )


def to_rows(legal_entity_id: int, facts: list[dict]) -> list[dict]:
    return [{
        "legal_entity_id": legal_entity_id,
        "period_end": f["period_end"],
        "metric": f["metric"],
        "value": f["value"],
        "currency": f.get("currency"),
        "scale": 1,
        "segment": "",
        "reliability": "A",
        "extracted_by": "registry_api",
        "verified": True,
        "notes": f"{f.get('label', '')} | {f.get('source_url', '')}".strip(" |"),
    } for f in facts]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--entity", help="registry:entity_id, e.g. ro_anaf:16896480 (default: all monitored)")
    ap.add_argument("--from-year", type=int)
    ap.add_argument("--to-year", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    service = RegistryService(user_agent=os.getenv("REGISTRY_USER_AGENT") or None)
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE")
    db = Supabase(url, key) if url and key else None
    if db is None and not args.dry_run:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE not set (use --dry-run to test)", file=sys.stderr)
        return 2

    if args.entity:
        registry, entity_id = args.entity.split(":", 1)
        targets = [{"id": None, "registry": registry, "entity_id": entity_id, "name": entity_id}]
        if db:
            match = [e for e in db.entities((registry,)) if e["entity_id"] == entity_id]
            if match:
                targets = match
            elif not args.dry_run:
                print(f"{args.entity} is not in legal_entity (monitor=true); add it first", file=sys.stderr)
                return 2
    else:
        targets = db.entities(FIGURE_REGISTRIES) if db else []

    total, failed = 0, 0
    for e in targets:
        try:
            facts = service.accounts(e["registry"], e["entity_id"], from_year=args.from_year, to_year=args.to_year)
        except RegistryError as exc:
            failed += 1
            print(f"FAIL {e['registry']}:{e['entity_id']} {exc}", file=sys.stderr)
            continue
        rows = to_rows(e["id"], facts)
        years = sorted({r["period_end"][:4] for r in rows})
        print(f"{e['registry']}:{e['entity_id']} {e.get('name', '')}: {len(rows)} facts, years {', '.join(years) or '-'}")
        if args.dry_run:
            print(json.dumps(rows[:5], ensure_ascii=False, indent=1))
        elif rows:
            db.upsert_facts(rows)
        total += len(rows)

    print(f"done: {total} facts{' (dry run, nothing written)' if args.dry_run else ' upserted'}, {failed} failed")
    return 1 if failed and not total else 0


if __name__ == "__main__":
    raise SystemExit(main())
