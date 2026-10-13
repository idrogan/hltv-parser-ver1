"""One adapter per registry. Each turns the registry's own format into a
list of normalized *filing* dicts:

    {
      "filing_uid":   "gb_ch:07751649:MzQ1...",   # stable, used for dedupe
      "registry":     "gb_ch",
      "entity_id":    "07751649",
      "filed_at":     "2026-09-30",              # YYYY-MM-DD
      "form":         "AA",                       # registry's own type code
      "category":     "accounts",                 # normalized, see CATEGORIES
      "description":  "Full accounts made up to 31 December 2025",
      "period_end":   "2025-12-31" | None,
      "document_url": "https://...pdf" | None,    # direct document if known
      "source_url":   "https://..."               # human-readable registry page
    }

Registries
----------
* ``gb_ch``    UK Companies House — free API key required (COMPANIES_HOUSE_API_KEY).
* ``dk_cvr``   Denmark, Erhvervsstyrelsen public filings index (distribution.virk.dk), no key.
* ``no_brreg`` Norway, Brønnøysund Enhetsregisteret + Regnskapsregisteret, no key.
* ``us_sec``   SEC EDGAR submissions API, no key; a contact User-Agent is mandatory.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .client import HttpClient, RegistryAuthError, RegistryError

log = logging.getLogger(__name__)

CATEGORIES = {
    "accounts",       # annual report / financial statements
    "interim",        # quarterly / half-year (SEC 10-Q, 6-K)
    "event",          # material event (SEC 8-K)
    "officers",       # directors appointed / resigned
    "capital",        # share capital changes, allotments
    "charges",        # mortgages / security interests
    "insolvency",     # liquidation, bankruptcy, strike-off
    "ownership",      # persons with significant control
    "other",
}

REGISTRIES = ("gb_ch", "dk_cvr", "no_brreg", "us_sec")


def _d(value: Optional[str]) -> Optional[str]:
    """Trim an ISO datetime/date string to YYYY-MM-DD."""
    if not value:
        return None
    return str(value)[:10]


# ---------------------------------------------------------------------------
# UK — Companies House
# ---------------------------------------------------------------------------

_CH_CATEGORY = {
    "accounts": "accounts",
    "officers": "officers",
    "capital": "capital",
    "mortgage": "charges",
    "insolvency": "insolvency",
    "liquidation": "insolvency",
    "dissolution": "insolvency",
    "persons-with-significant-control": "ownership",
}


class CompaniesHouse:
    registry = "gb_ch"
    API = "https://api.company-information.service.gov.uk"
    WEB = "https://find-and-update.company-information.service.gov.uk"

    def __init__(self, http: HttpClient, api_key: Optional[str]):
        self.http = http
        self.api_key = api_key

    def filings(self, entity_id: str, limit: int = 50) -> list[dict]:
        if not self.api_key:
            raise RegistryAuthError(
                "COMPANIES_HOUSE_API_KEY is not set — register a free key at "
                "https://developer.company-information.service.gov.uk/"
            )
        number = entity_id.strip().upper()
        data = self.http.request_json(
            "GET",
            f"{self.API}/company/{number}/filing-history",
            params={"items_per_page": limit},
            auth=(self.api_key, ""),  # key is the basic-auth username, empty password
        )
        out = []
        for it in data.get("items", []) or []:
            tid = it.get("transaction_id")
            if not tid:
                continue
            desc_values = it.get("description_values") or {}
            category = _CH_CATEGORY.get(it.get("category", ""), "other")
            has_doc = bool((it.get("links") or {}).get("document_metadata"))
            out.append({
                "filing_uid": f"{self.registry}:{number}:{tid}",
                "registry": self.registry,
                "entity_id": number,
                "filed_at": _d(it.get("date")),
                "form": it.get("type"),
                "category": category,
                "description": _ch_describe(it.get("description"), desc_values),
                "period_end": _d(desc_values.get("made_up_date")),
                "document_url": (
                    f"{self.WEB}/company/{number}/filing-history/{tid}/document?format=pdf&download=0"
                    if has_doc else None
                ),
                "source_url": f"{self.WEB}/company/{number}/filing-history",
            })
        return out


def _ch_describe(code: Optional[str], values: dict) -> str:
    """Companies House returns description *codes* (e.g. ``accounts-with-accounts-type-full``).
    Make them readable without pulling the full enum file."""
    if not code:
        return ""
    text = code.replace("-", " ")
    extra = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in values.items() if isinstance(v, str))
    return f"{text} ({extra})" if extra else text


# ---------------------------------------------------------------------------
# Denmark — Erhvervsstyrelsen public filings (annual reports)
# ---------------------------------------------------------------------------

class DanishCVR:
    registry = "dk_cvr"
    # The public Elasticsearch index only answers over plain HTTP.
    SEARCH = "http://distribution.virk.dk/offentliggoerelser/_search"

    def __init__(self, http: HttpClient):
        self.http = http

    def filings(self, entity_id: str, limit: int = 20) -> list[dict]:
        cvr = entity_id.strip()
        body = {
            "query": {"term": {"cvrNummer": int(cvr)}},
            "sort": [{"offentliggoerelsesTidspunkt": {"order": "desc"}}],
            "size": limit,
        }
        data = self.http.request_json("POST", self.SEARCH, json_body=body)
        out = []
        for hit in ((data.get("hits") or {}).get("hits") or []):
            src = hit.get("_source") or {}
            period = ((src.get("regnskab") or {}).get("regnskabsperiode") or {})
            docs = src.get("dokumenter") or []
            pdf = next(
                (d.get("dokumentUrl") for d in docs if "pdf" in str(d.get("dokumentMimeType", "")).lower()),
                None,
            ) or next((d.get("dokumentUrl") for d in docs if d.get("dokumentUrl")), None)
            published = src.get("offentliggoerelsesTidspunkt")
            kind = src.get("offentliggoerelsestype") or "regnskab"
            correction = bool(src.get("omgoerelse"))
            out.append({
                "filing_uid": f"{self.registry}:{cvr}:{hit.get('_id') or published}",
                "registry": self.registry,
                "entity_id": cvr,
                "filed_at": _d(published),
                "form": kind,
                "category": "accounts" if "regnskab" in kind.lower() else "other",
                "description": (
                    f"Årsrapport {_d(period.get('startDato'))} – {_d(period.get('slutDato'))}"
                    + (" (omgørelse / correction)" if correction else "")
                ),
                "period_end": _d(period.get("slutDato")),
                "document_url": pdf,
                "source_url": f"https://datacvr.virk.dk/enhed/virksomhed/{cvr}",
            })
        return out


# ---------------------------------------------------------------------------
# Norway — Brønnøysund
# ---------------------------------------------------------------------------

class Brreg:
    registry = "no_brreg"
    ENHET = "https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}"
    REGNSKAP = "https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}"

    def __init__(self, http: HttpClient):
        self.http = http

    def filings(self, entity_id: str, limit: int = 20) -> list[dict]:
        orgnr = entity_id.replace(" ", "").strip()
        enhet = self.http.request_json("GET", self.ENHET.format(orgnr=orgnr))
        out = []
        last_year = enhet.get("sisteInnsendteAarsregnskap")
        source_url = f"https://virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}"
        if last_year:
            out.append({
                "filing_uid": f"{self.registry}:{orgnr}:aarsregnskap:{last_year}",
                "registry": self.registry,
                "entity_id": orgnr,
                # Brreg does not expose the submission date here; use 31 Dec of the
                # fiscal year as a stable placeholder. The *new uid* is what triggers alerts.
                "filed_at": None,
                "form": "aarsregnskap",
                "category": "accounts",
                "description": f"Årsregnskap {last_year} innsendt",
                "period_end": f"{last_year}-12-31",
                "document_url": None,
                "source_url": source_url,
            })
        for flag, label in (("konkurs", "Konkurs (bankruptcy)"),
                            ("underAvvikling", "Under avvikling (winding up)"),
                            ("underTvangsavviklingEllerTvangsopplosning", "Tvangsavvikling (forced dissolution)")):
            if enhet.get(flag):
                out.append({
                    "filing_uid": f"{self.registry}:{orgnr}:{flag}",
                    "registry": self.registry,
                    "entity_id": orgnr,
                    "filed_at": _d(enhet.get(f"{flag}sdato") or enhet.get("konkursdato")),
                    "form": flag,
                    "category": "insolvency",
                    "description": label,
                    "period_end": None,
                    "document_url": None,
                    "source_url": source_url,
                })
        return out[:limit]

    def accounts(self, entity_id: str) -> list[dict]:
        """Key figures from Regnskapsregisteret (latest year(s) Brreg serves).

        The API is labelled *preview* by Brreg, so every field is read defensively.
        """
        orgnr = entity_id.replace(" ", "").strip()
        data = self.http.request_json("GET", self.REGNSKAP.format(orgnr=orgnr))
        rows = data if isinstance(data, list) else [data]
        out = []
        for r in rows:
            period = r.get("regnskapsperiode") or {}
            res = r.get("resultatregnskapResultat") or {}
            drift = res.get("driftsresultat") or {}
            inntekter = drift.get("driftsinntekter") or {}
            balanse = r.get("egenkapitalGjeld") or {}
            ek = balanse.get("egenkapital") or {}
            eiendeler = r.get("eiendeler") or {}
            out.append({
                "entity_id": orgnr,
                "period_start": _d(period.get("fraDato")),
                "period_end": _d(period.get("tilDato")),
                "currency": r.get("valuta"),
                "revenue": inntekter.get("sumDriftsinntekter"),
                "operating_result": drift.get("driftsresultat"),
                "net_result": res.get("aarsresultat"),
                "equity": ek.get("sumEgenkapital"),
                "total_assets": eiendeler.get("sumEiendeler"),
                "source_url": self.REGNSKAP.format(orgnr=orgnr),
            })
        return out


# ---------------------------------------------------------------------------
# USA — SEC EDGAR
# ---------------------------------------------------------------------------

_SEC_CATEGORY = {
    "10-K": "accounts", "10-K/A": "accounts", "20-F": "accounts", "20-F/A": "accounts",
    "40-F": "accounts",
    "10-Q": "interim", "10-Q/A": "interim", "6-K": "interim",
    "8-K": "event", "8-K/A": "event",
    "SC 13D": "ownership", "SC 13G": "ownership", "SC 13D/A": "ownership", "SC 13G/A": "ownership",
    "DEF 14A": "other", "S-1": "capital", "F-1": "capital", "424B4": "capital", "S-3": "capital", "F-3": "capital",
}


class SecEdgar:
    registry = "us_sec"
    SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"

    def __init__(self, http: HttpClient):
        self.http = http

    def filings(self, entity_id: str, limit: int = 50) -> list[dict]:
        cik = str(int(entity_id))  # drop leading zeros for archive paths
        data = self.http.request_json("GET", self.SUBMISSIONS.format(cik=cik.zfill(10)))
        recent = ((data.get("filings") or {}).get("recent") or {})
        n = len(recent.get("accessionNumber") or [])
        out = []
        for i in range(min(n, limit)):
            def col(name: str) -> Any:
                values = recent.get(name) or []
                return values[i] if i < len(values) else None

            acc = col("accessionNumber")
            form = col("form") or ""
            acc_nodash = (acc or "").replace("-", "")
            primary = col("primaryDocument")
            out.append({
                "filing_uid": f"{self.registry}:{cik}:{acc}",
                "registry": self.registry,
                "entity_id": cik,
                "filed_at": _d(col("filingDate")),
                "form": form,
                "category": _SEC_CATEGORY.get(form, "other"),
                "description": col("primaryDocDescription") or form,
                "period_end": _d(col("reportDate")) or None,
                "document_url": (
                    f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{primary}" if primary else None
                ),
                "source_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
            })
        return out
