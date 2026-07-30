"""
Test offline (dati sintetici, niente rete) per i filing SEC recenti mostrati
nella scheda titolo.

Due livelli:
  - parse_recent_filings(): funzione pura, riceve il JSON submissions gia'
    scaricato. Si prova direttamente, senza finzioni di rete.
  - fetch_recent_filings(): la variante che fa la richiesta. Qui sostituiamo
    _sec_get, cioe' l'unico punto in cui il modulo parla con la SEC.
"""
import os
os.environ.setdefault("SEC_USER_AGENT", "Lynch Test test@example.com")

import data_sources as ds


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def make_submissions(forms, accns, filing_dates, report_dates, docs, cik="0000320193"):
    return {"cik": cik, "filings": {"recent": {
        "form": forms, "accessionNumber": accns,
        "filingDate": filing_dates, "reportDate": report_dates,
        "primaryDocument": docs,
    }}}


SUBMISSIONS = make_submissions(
    forms=["8-K", "10-Q", "10-Q", "10-K", "10-Q", "10-Q", "10-K"],
    accns=["0000320193-26-000010", "0000320193-26-000009", "0000320193-26-000008",
           "0000320193-25-000110", "0000320193-25-000090", "0000320193-25-000070",
           "0000320193-24-000106"],
    filing_dates=["2026-07-20", "2026-07-10", "2026-04-15", "2025-11-01",
                  "2025-08-01", "2025-05-01", "2024-11-01"],
    report_dates=["2026-06-30", "2026-06-30", "2026-03-28", "2025-09-27",
                  "2025-06-28", "2025-03-29", "2024-09-28"],
    docs=["aapl-form8k.htm", "aapl-20260630.htm", "aapl-20260328.htm",
          "aapl-20250927.htm", "aapl-20250628.htm", "aapl-20250329.htm",
          "aapl-20240928.htm"],
)

print("=" * 72)
print("TEST 1 — parser puro: solo 10-K/10-Q, scarta 8-K, si ferma a N")
print("=" * 72)
out = ds.parse_recent_filings(SUBMISSIONS, "0000320193", n=5)
print(f"  {len(out)} filing ritornati (atteso 5, l'8-K va scartato)")
assert len(out) == 5
assert all(f["form"] in ("10-K", "10-Q") for f in out)
assert out[0]["form"] == "10-Q" and out[0]["filing_date"] == "2026-07-10"
assert out[0]["period_date"] == "2026-06-30"
print(f"  primo filing: {out[0]['form']} depositato {out[0]['filing_date']}, "
      f"periodo {out[0]['period_date']}")
print(f"  url: {out[0]['url']}")
assert out[0]["url"] == ("https://www.sec.gov/Archives/edgar/data/320193/"
                         "000032019326000009/aapl-20260630.htm")
print("  ✓ Scarta 8-K, si ferma a 5, URL Archives senza zeri iniziali nel CIK")

print("\n" + "=" * 72)
print("TEST 2 — submissions vuoto o assente: lista vuota, nessuna eccezione")
print("=" * 72)
assert ds.parse_recent_filings(None, "0000320193") == []
assert ds.parse_recent_filings({}, "0000320193") == []
assert ds.parse_recent_filings(SUBMISSIONS, None) != []      # il cik sta nel payload
assert ds.parse_recent_filings(SUBMISSIONS, "non-numerico") == []
print("  ✓ Ogni forma degenere ritorna una lista vuota")

print("\n" + "=" * 72)
print("TEST 3 — fetch_recent_filings con la rete sostituita")
print("=" * 72)
ds._ticker_cik_cache = {"AAPL": "0000320193"}
orig = ds._sec_get
ds._sec_get = lambda url, timeout=30, retries=3: FakeResponse(200, SUBMISSIONS)
try:
    out = ds.fetch_recent_filings("AAPL", n=3)
finally:
    ds._sec_get = orig
assert len(out) == 3 and out[0]["form"] == "10-Q"
print("  ✓ Passa dal solo punto di contatto con la SEC (_sec_get)")

print("\n" + "=" * 72)
print("TEST 4 — variante ticker con trattino/punto (es. BRK-B / BRK.B)")
print("=" * 72)
ds._ticker_cik_cache = {"BRK-B": "0001067983"}
submissions_b = make_submissions(
    forms=["10-K"], accns=["0001067983-26-000005"],
    filing_dates=["2026-03-01"], report_dates=["2025-12-31"],
    docs=["brkb-20251231.htm"], cik="0001067983",
)
ds._sec_get = lambda url, timeout=30, retries=3: FakeResponse(200, submissions_b)
try:
    out = ds.fetch_recent_filings("BRK.B", n=5)
finally:
    ds._sec_get = orig
assert len(out) == 1 and out[0]["form"] == "10-K"
print("  ✓ Risolve 'BRK.B' anche se la mappa CIK ha 'BRK-B'")

print("\n" + "=" * 72)
print("TEST 5 — anagrafica: SIC -> settore e industria")
print("=" * 72)
meta = ds.parse_company_meta({"name": "Apple Inc.", "sic": "3571",
                              "sicDescription": "Electronic Computers",
                              "exchanges": ["Nasdaq"], "entityType": "operating"})
assert meta["sic"] == "3571" and meta["exchange"] == "Nasdaq"
from sic_map import sector_from_sic, industry_from_sic
assert sector_from_sic("3571") == "Technology", sector_from_sic("3571")
assert industry_from_sic("3571") == "Computer Hardware"
assert sector_from_sic("6022") == "Financial Services"
assert sector_from_sic("2834") == "Healthcare"
assert sector_from_sic("4911") == "Utilities"
assert sector_from_sic("6798") == "Real Estate"          # REIT, non finanziaria
assert sector_from_sic("1311") == "Energy"
assert sector_from_sic(None) is None and sector_from_sic("") is None
assert sector_from_sic("banana") is None
# codice non in tabella: si usa la descrizione ufficiale della SEC
assert industry_from_sic("9995", "Non-Classifiable Establishments") == \
    "Non-Classifiable Establishments"
assert industry_from_sic(None, None) is None
print("  ✓ Ogni codice SIC cade nel settore giusto; i casi vuoti danno None")

print("\n" + "=" * 72)
print("TEST 6 — campi del provider: stringhe e infiniti non devono passare")
print("=" * 72)
# Caso reale: per BILL yfinance restituisce la STRINGA 'Infinity' in trailingPE
# quando l'utile per azione e' zero. Il valore attraversava il modulo travestito
# da numero e faceva esplodere il primo confronto, perdendo il ticker.
assert ds._finite("Infinity") is None, ds._finite("Infinity")
assert ds._finite(float("inf")) is None
assert ds._finite(float("-inf")) is None
assert ds._finite(float("nan")) is None
assert ds._finite(None) is None
assert ds._finite("non un numero") is None
assert ds._finite(True) is None          # i bool non sono misure
assert ds._finite("14.03") == 14.03      # una stringa numerica si converte
assert ds._finite(0) == 0.0              # zero e' un valore, non un'assenza
assert ds._finite(-3.5) == -3.5
print("  ✓ 'Infinity', NaN, infiniti, bool e testo diventano None; i numeri passano")

comparisons = [ds._finite(v) for v in ("Infinity", None, "12.5", 0)]
for v in comparisons:
    # il punto del test: dopo la normalizzazione ogni valore e' confrontabile
    assert v is None or (v > 0 or v <= 0)
print("  ✓ Dopo la normalizzazione ogni valore e' confrontabile senza TypeError")

ds._ticker_cik_cache = None   # stato pulito per eventuali altri test

print("\n" + "=" * 72)
print("✅ TUTTI I TEST SUI FILING RECENTI PASSATI")
print("=" * 72)
