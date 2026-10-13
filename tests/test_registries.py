"""Registry adapters: normalization of each registry's format.

The HTTP layer is replaced by a fake that returns fixture payloads shaped
like the real APIs, so these tests hit no network.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from registries.client import RegistryAuthError, RegistryError
from registries.service import RegistryService


class FakeHttp:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple] = []

    def request_json(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for needle, payload in self.routes.items():
            if needle in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise RegistryError(f"no fixture for {url}")


CH_PAYLOAD = {
    "items": [
        {
            "transaction_id": "MzQ1Njc4OTAxMmFkaXF6a2N4",
            "date": "2026-09-30",
            "category": "accounts",
            "type": "AA",
            "description": "accounts-with-accounts-type-full",
            "description_values": {"made_up_date": "2025-12-31"},
            "links": {"document_metadata": "https://document-api.company-information.service.gov.uk/document/abc"},
        },
        {
            "transaction_id": "MzQ1Njc4OTAxMmFkaXF6a2N5",
            "date": "2026-06-01",
            "category": "officers",
            "type": "TM01",
            "description": "termination-director-company-with-name-termination-date",
            "description_values": {"officer_name": "Jane Doe", "termination_date": "2026-05-31"},
            "links": {},
        },
    ]
}

CVR_PAYLOAD = {
    "hits": {
        "hits": [
            {
                "_id": "38074466-2026-05-20",
                "_source": {
                    "cvrNummer": 38074466,
                    "offentliggoerelsesTidspunkt": "2026-05-20T10:15:00.000Z",
                    "offentliggoerelsestype": "regnskab",
                    "omgoerelse": False,
                    "regnskab": {"regnskabsperiode": {"startDato": "2025-01-01", "slutDato": "2025-12-31"}},
                    "dokumenter": [
                        {"dokumentUrl": "http://regnskaber.virk.dk/x.xml", "dokumentMimeType": "application/xml"},
                        {"dokumentUrl": "http://regnskaber.virk.dk/x.pdf", "dokumentMimeType": "application/pdf"},
                    ],
                },
            }
        ]
    }
}

BRREG_ENHET = {"organisasjonsnummer": "924115904", "navn": "HEROIC AS", "sisteInnsendteAarsregnskap": "2025",
               "konkurs": False, "underAvvikling": False}

BRREG_REGNSKAP = [{
    "regnskapsperiode": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
    "valuta": "NOK",
    "resultatregnskapResultat": {
        "aarsresultat": -28000000,
        "driftsresultat": {"driftsresultat": -30100000, "driftsinntekter": {"sumDriftsinntekter": 72500000}},
    },
    "egenkapitalGjeld": {"egenkapital": {"sumEgenkapital": 5000000}},
    "eiendeler": {"sumEiendeler": 40000000},
}]

SEC_PAYLOAD = {
    "filings": {
        "recent": {
            "accessionNumber": ["0001493152-26-019771", "0001641172-26-000001"],
            "filingDate": ["2026-04-30", "2026-08-15"],
            "reportDate": ["2025-12-31", ""],
            "form": ["20-F", "6-K"],
            "primaryDocument": ["form20-f.htm", "form6-k.htm"],
            "primaryDocDescription": ["ANNUAL REPORT", "REPORT OF FOREIGN ISSUER"],
        }
    }
}


def _service(routes: dict, ch_key: str | None = "key") -> tuple[RegistryService, FakeHttp]:
    http = FakeHttp(routes)
    return RegistryService(companies_house_key=ch_key, http=http), http


def test_companies_house_normalizes_and_uses_basic_auth():
    svc, http = _service({"/company/07751649/filing-history": CH_PAYLOAD})
    rows = svc.filings("gb_ch", "07751649")
    assert [r["category"] for r in rows] == ["accounts", "officers"]
    acc = rows[0]
    assert acc["filing_uid"] == "gb_ch:07751649:MzQ1Njc4OTAxMmFkaXF6a2N4"
    assert acc["period_end"] == "2025-12-31"
    assert acc["document_url"].endswith("/filing-history/MzQ1Njc4OTAxMmFkaXF6a2N4/document?format=pdf&download=0")
    assert rows[1]["document_url"] is None
    assert http.calls[0][2]["auth"] == ("key", "")


def test_companies_house_without_key_fails_clearly():
    svc, _ = _service({}, ch_key=None)
    with pytest.raises(RegistryAuthError):
        svc.filings("gb_ch", "07751649")


def test_cvr_prefers_pdf_and_queries_by_int_cvr():
    svc, http = _service({"distribution.virk.dk": CVR_PAYLOAD})
    rows = svc.filings("dk_cvr", "38074466")
    assert len(rows) == 1
    r = rows[0]
    assert r["document_url"].endswith(".pdf")
    assert r["filed_at"] == "2026-05-20"
    assert r["period_end"] == "2025-12-31"
    assert r["category"] == "accounts"
    assert http.calls[0][0] == "POST"
    assert http.calls[0][2]["json_body"]["query"] == {"term": {"cvrNummer": 38074466}}


def test_brreg_turns_last_submitted_year_into_filing():
    svc, _ = _service({"enhetsregisteret": BRREG_ENHET})
    rows = svc.filings("no_brreg", "924 115 904")
    assert rows == [{
        "filing_uid": "no_brreg:924115904:aarsregnskap:2025",
        "registry": "no_brreg",
        "entity_id": "924115904",
        "filed_at": None,
        "form": "aarsregnskap",
        "category": "accounts",
        "description": "Årsregnskap 2025 innsendt",
        "period_end": "2025-12-31",
        "document_url": None,
        "source_url": "https://virksomhet.brreg.no/nb/oppslag/enheter/924115904",
    }]


def test_brreg_flags_insolvency():
    enhet = dict(BRREG_ENHET, konkurs=True, konkursdato="2026-10-01")
    svc, _ = _service({"enhetsregisteret": enhet})
    rows = svc.filings("no_brreg", "924115904")
    assert any(r["category"] == "insolvency" and r["form"] == "konkurs" for r in rows)


def test_brreg_accounts_key_figures():
    svc, _ = _service({"regnskapsregisteret": BRREG_REGNSKAP})
    [acc] = svc.brreg_accounts("924115904")
    assert acc["revenue"] == 72500000
    assert acc["operating_result"] == -30100000
    assert acc["net_result"] == -28000000
    assert acc["currency"] == "NOK"
    assert acc["period_end"] == "2025-12-31"


def test_sec_builds_archive_url_and_pads_cik():
    svc, http = _service({"data.sec.gov": SEC_PAYLOAD})
    rows = svc.filings("us_sec", "0001966233")
    assert http.calls[0][1].endswith("CIK0001966233.json")
    annual = rows[0]
    assert annual["category"] == "accounts"
    assert annual["document_url"] == (
        "https://www.sec.gov/Archives/edgar/data/1966233/000149315226019771/form20-f.htm"
    )
    assert rows[1]["category"] == "interim"
    assert rows[1]["period_end"] is None


def test_since_filter_keeps_undated_rows():
    svc, _ = _service({"data.sec.gov": SEC_PAYLOAD, "enhetsregisteret": BRREG_ENHET})
    res = svc.batch(
        [{"registry": "us_sec", "entity_id": "1966233", "legal_entity_id": 7, "name": "NIP Group Inc."},
         {"registry": "no_brreg", "entity_id": "924115904"}],
        since="2026-06-01",
    )
    forms = {r["form"] for r in res["filings"]}
    assert forms == {"6-K", "aarsregnskap"}  # 20-F from April dropped, undated Brreg kept
    sec = next(r for r in res["filings"] if r["registry"] == "us_sec")
    assert sec["legal_entity_id"] == 7 and sec["entity_name"] == "NIP Group Inc."


def test_batch_isolates_errors():
    svc, _ = _service({"data.sec.gov": SEC_PAYLOAD, "enhetsregisteret": RegistryError("404")})
    res = svc.batch([{"registry": "no_brreg", "entity_id": "1"}, {"registry": "us_sec", "entity_id": "1966233"},
                     {"registry": "xx", "entity_id": "1"}])
    assert res["count"] == 2
    assert {e["registry"] for e in res["errors"]} == {"no_brreg", "xx"}



@pytest.mark.asyncio
async def test_batch_endpoint_requires_auth_and_validates(fresh_app):
    app = fresh_app(API_TOKEN="abc123", API_REQUIRE_TOKEN="true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.post("/registries/filings", json={"entities": []})
        assert r.status_code == 401
        r = await ac.post("/registries/filings", json={"entities": []},
                          headers={"Authorization": "Bearer abc123"})
        assert r.status_code == 200
        assert r.json() == {"since": None, "count": 0, "filings": [], "errors": []}
        r = await ac.post("/registries/filings", json={"nope": 1}, headers={"Authorization": "Bearer abc123"})
        assert r.status_code == 422
