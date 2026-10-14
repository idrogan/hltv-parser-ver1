"""Loader: registry facts -> financial_fact rows -> Supabase upsert call."""
from __future__ import annotations

import json

from tools import load_financials as lf


def test_to_rows_marks_registry_figures_as_verified_a():
    rows = lf.to_rows(9, [{"period_end": "2024-12-31", "metric": "revenue", "value": 1.0, "currency": "RON",
                           "label": "Cifra de afaceri neta", "source_url": "https://x/bilant?an=2024&cui=1"}])
    assert rows == [{
        "legal_entity_id": 9, "period_end": "2024-12-31", "metric": "revenue", "value": 1.0, "currency": "RON",
        "scale": 1, "segment": "", "reliability": "A", "extracted_by": "registry_api", "verified": True,
        "notes": "Cifra de afaceri neta | https://x/bilant?an=2024&cui=1",
    }]


def test_supabase_upsert_uses_conflict_key_and_merge(monkeypatch):
    sent = []

    class Resp:
        def __init__(self, body=b""):
            self.body = body
        def read(self):
            return self.body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        sent.append(req)
        return Resp()

    monkeypatch.setattr(lf.urllib.request, "urlopen", fake_urlopen)
    db = lf.Supabase("https://p.supabase.co/", "k")
    db.upsert_facts([{"a": i} for i in range(501)])
    assert len(sent) == 2                                    # batches of 500
    r = sent[0]
    assert r.full_url == ("https://p.supabase.co/rest/v1/financial_fact"
                          "?on_conflict=legal_entity_id,period_end,metric,segment")
    assert r.get_header("Prefer") == "resolution=merge-duplicates,return=minimal"
    assert r.get_header("Apikey") == "k"
    assert len(json.loads(r.data)) == 500


def test_dry_run_without_supabase(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    class Svc:
        def __init__(self, **kw):
            pass
        def accounts(self, registry, entity_id, from_year=None, to_year=None):
            return [{"period_end": "2024-12-31", "metric": "revenue", "value": 5, "currency": "RON",
                     "label": "x", "source_url": "u"}]

    monkeypatch.setattr(lf, "RegistryService", Svc)
    assert lf.main(["--entity", "ro_anaf:16896480", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "1 facts, years 2024" in out and "nothing written" in out
