#!/usr/bin/env python3
"""
overview.py — La scheda di apertura: che azienda e', e cosa ne dice il mercato.

PERCHE' STA PER PRIMA. Tutto il resto dell'applicazione risponde a domande sui
NUMERI di una societa'; nessuna pagina diceva che cosa quella societa' faccia.
Si poteva leggere il margine operativo di Alcoa, il suo ROIC e il suo debito
senza mai incontrare la parola "alluminio" — e un multiplo giudicato senza
sapere che mestiere fa l'azienda e' un numero senza contesto.

Ha preso il posto della scheda "Growth (5y)", che duplicava la sotto-scheda
omonima dentro Financials. Il contenuto che quella scheda aveva di suo — la
misura di crescita che determina il multiplo, la matrice a 3/5/10 anni, i
valori per azione e l'audit degli estremi — non e' stato buttato: e' stato
spostato dentro la sotto-scheda che la sostituisce.

TUTTO QUI DENTRO ARRIVA DALLA RETE AL MOMENTO. Descrizione, obiettivi degli
analisti, giudizi e notizie non stanno nel dataset e non possono starci: sono
dati che cambiano ogni giorno e che nessun deposito alla SEC contiene. Si
scaricano su richiesta e restano in cache un'ora, come le candele del grafico
del prezzo. Quando il provider non risponde, ogni riquadro lo dichiara da
solo invece di sparire.

LA RIPARTIZIONE DEI RICAVI viene invece dai depositi, e la legge `segments.py`:
per divisione, per area e per prodotto, esattamente come la societa' la
comunica alla SEC. Non e' nelle API `companyfacts` che il resto
dell'applicazione usa — quelle danno solo i totali consolidati — ma un livello
piu' sotto, nelle dimensioni del documento XBRL allegato al bilancio annuale.

DOVE UN DATO MANCA, MANCA LA SEZIONE. Non ci sono note fisse che spiegano
un'assenza: ripetute su novecento titoli non sono una spiegazione, sono rumore
che occupa il posto di un'informazione. L'unica eccezione e' la configurazione
mancante (il contatto per la SEC), che non riguarda la societa' ma
l'applicazione, e che taciuta lascerebbe una funzione invisibile per sempre.
"""

from __future__ import annotations

import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import charts

try:
    import segments as segments_mod
    HAS_SEGMENTS = True
except ImportError:
    segments_mod = None
    HAS_SEGMENTS = False

TONE_ICON = {"bull": "🟢", "bear": "🔴", "flag": "🟡", "neutral": "⚪"}


def _split_summary(text: str) -> tuple[str, str]:
    """
    La prima frase da sola, il resto a parte.

    La prima frase di una descrizione depositata dice sempre il mestiere
    ("Alcoa Corporation... engages in the bauxite mining, alumina refining,
    aluminum production..."); le quattordici righe successive elencano
    controllate e clientele. Separarle e' la differenza fra una riga che si
    legge e un paragrafo che si salta.
    """
    text = (text or "").strip()
    if not text:
        return "", ""
    cut = re.search(r"(?<=[.!?])\s+(?=[A-Z])", text)
    if not cut or cut.start() > 400:
        return text[:400] + ("…" if len(text) > 400 else ""), text
    return text[:cut.start()].strip(), text[cut.end():].strip()


def _render_segments(ticker: str, cik_str: str) -> None:
    """Le ripartizioni dei ricavi depositate nell'ultimo bilancio annuale."""
    # DUE SILENZI DIVERSI, E VANNO DISTINTI.
    #
    # Una societa' che non deposita la ripartizione non merita una nota: la
    # sezione semplicemente non c'e'. Ma un'APPLICAZIONE senza contatto per la
    # SEC non e' un caso particolare della societa': e' la stessa configurazione
    # che manca su ogni titolo, e taciuta produrrebbe una funzione che non
    # compare mai senza che nessuno sappia perche'.
    ua = segments_mod.ds.sec_user_agent()
    if not ua or "@" not in ua:
        st.divider()
        st.markdown("#### Where the revenue comes from")
        st.info(
            "**The revenue breakdown needs a contact address for the SEC.** "
            "Their APIs are free and need no key, but they do ask who is "
            "calling: without it they answer 403 and this section stays empty "
            "on every ticker. Set `SEC_USER_AGENT` — locally with "
            "`echo 'Lynch Research you@email.com' > .sec_user_agent`, on "
            "Streamlit Cloud as an environment variable in the app settings. "
            "It is the same variable the dataset build already uses.",
            icon="🔑")
        return

    data = segments_mod.fetch_segments(ticker, cik_str)
    if not data:
        return

    st.divider()
    st.markdown("#### Where the revenue comes from")
    meta = data.get("_meta", {})
    st.caption(
        "Read out of the **segment note of the latest annual report**, filed "
        f"{meta.get('filed', '—')}. These are the divisions and the regions the "
        "company itself reports to the SEC — not an estimate, and not a "
        "classification made by this app.")

    blocks = [(k, v) for k, v in data.items() if k != "_meta"]
    cols = st.columns(min(len(blocks), 2)) if len(blocks) > 1 else [st]
    for i, (axis_key, blk) in enumerate(blocks):
        target = cols[i % len(cols)] if len(blocks) > 1 else st
        with target:
            end = sorted(blk["periods"], reverse=True)[0]
            members = blk["periods"][end]
            st.markdown(f"**{blk['title']}** · FY {end[:4]}")
            fig = charts.donut(list(members), list(members.values()))
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True,
                                key=f"seg_{ticker}_{axis_key}")
            tone, note = segments_mod.concentration_note(members, blk["word"])
            if note:
                st.markdown(f"{TONE_ICON.get(tone, '⚪')}&nbsp; {note}")
            growth = segments_mod.growth_note(blk["periods"])
            if growth:
                st.markdown(f"📈&nbsp; {growth}")
            st.caption(blk["note"])

    st.caption(
        "Every breakdown shown here has been **checked against the "
        "consolidated revenue** of the same year: where the parts did not add "
        "up to the whole — because the company tags a subtotal alongside its "
        "components — the offending line is removed, and where the sum still "
        "refuses to reconcile nothing is drawn at all. A pie chart that does "
        "not add up looks like information and is an error.")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_profile(ticker: str) -> dict | None:
    """Descrizione, settore, sede, dipendenti, sito. Cache di un'ora."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception:                                        # noqa: BLE001
        return None
    if not info.get("longBusinessSummary") and not info.get("longName"):
        return None
    return {k: info.get(k) for k in (
        "longName", "longBusinessSummary", "sector", "industry", "country",
        "city", "state", "website", "fullTimeEmployees", "exchange",
        "currency", "quoteType")}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_analysts(ticker: str) -> dict | None:
    """Obiettivi di prezzo, numero di analisti e distribuzione dei giudizi."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        targets = {}
        try:
            targets = t.analyst_price_targets or {}
        except Exception:                                    # noqa: BLE001
            targets = {}
        recs = None
        try:
            r = t.recommendations
            if r is not None and not r.empty:
                recs = r.to_dict("records")
        except Exception:                                    # noqa: BLE001
            recs = None
        grades = None
        try:
            g = t.upgrades_downgrades
            if g is not None and not g.empty:
                g = g.reset_index().head(12)
                grades = g.to_dict("records")
        except Exception:                                    # noqa: BLE001
            grades = None
    except Exception:                                        # noqa: BLE001
        return None

    def _n(*keys):
        for k in keys:
            v = targets.get(k) if k in targets else info.get(k)
            try:
                f = float(v)
                if f == f and f > 0:
                    return f
            except (TypeError, ValueError):
                continue
        return None

    out = {
        "current": _n("current", "currentPrice"),
        "low": _n("low", "targetLowPrice"),
        "mean": _n("mean", "targetMeanPrice"),
        "median": _n("median", "targetMedianPrice"),
        "high": _n("high", "targetHighPrice"),
        "n_analysts": info.get("numberOfAnalystOpinions"),
        "recommendation": info.get("recommendationKey"),
        "recommendation_mean": info.get("recommendationMean"),
        "recs": recs, "grades": grades,
    }
    return out if out["mean"] or out["recs"] else None


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_news(ticker: str, limit: int = 12) -> list[dict] | None:
    """Le notizie recenti, appiattite in righe con titolo, fonte, data e link."""
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).news or []
    except Exception:                                        # noqa: BLE001
        return None
    rows = []
    for item in raw[:limit]:
        c = item.get("content") or item
        url = ((c.get("clickThroughUrl") or {}).get("url")
               or (c.get("canonicalUrl") or {}).get("url") or c.get("link"))
        if not c.get("title") or not url:
            continue
        rows.append({
            "date": (c.get("pubDate") or c.get("displayTime") or "")[:10],
            "source": (c.get("provider") or {}).get("displayName", "—"),
            "title": c.get("title"),
            "summary": (c.get("summary") or c.get("description") or "")[:220],
            "url": url,
        })
    return rows or None


def target_chart(a: dict, height: int = 200) -> go.Figure | None:
    """
    Gli obiettivi degli analisti contro il prezzo di oggi, su un asse solo.

    UNA SCALA DI PREZZI, non barre. La domanda e' "dove sta il prezzo di adesso
    rispetto a quello che si aspettano", e la risposta si legge su una riga:
    l'intervallo fra minimo e massimo, i due valori centrali, e il punto in cui
    il mercato scambia oggi. Con delle barre affiancate quella distanza
    andrebbe ricostruita a mente confrontando altezze.
    """
    p = charts.palette()
    lo, hi = a.get("low"), a.get("high")
    mean, med, cur = a.get("mean"), a.get("median"), a.get("current")
    if not (lo and hi and cur):
        return None

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[lo, hi], y=[0, 0], mode="lines",
        line=dict(color=p["grid"], width=14), hoverinfo="skip",
        showlegend=False))
    for value, name, color, symbol in (
            (lo, "Lowest target", p["muted"], "line-ns"),
            (hi, "Highest target", p["muted"], "line-ns"),
            (med, "Median target", p["series"][1], "diamond"),
            (mean, "Mean target", p["series"][3], "circle"),
            (cur, "Price today", p["series"][0], "circle")):
        if not value:
            continue
        fig.add_trace(go.Scatter(
            x=[value], y=[0], mode="markers", name=name,
            marker=dict(color=color, size=18 if symbol != "line-ns" else 26,
                        symbol=symbol,
                        line=dict(width=2, color=p["surface"])),
            hovertemplate=f"{name}: $%{{x:,.2f}}<extra></extra>"))
        fig.add_annotation(x=value, y=0.42, text=f"${value:,.0f}",
                           showarrow=False, font=dict(size=11, color=p["muted"]))

    charts.style(fig, height=height, legend=True, hovermode="closest")
    fig.update_yaxes(visible=False, range=[-0.7, 0.9])
    fig.update_xaxes(showgrid=False, tickprefix="$",
                     tickfont=dict(color=p["muted"]))
    return fig


def _rating_bar(recs: list[dict], p: dict) -> go.Figure | None:
    """Distribuzione dei giudizi del mese piu' recente, in una barra impilata."""
    if not recs:
        return None
    cur = recs[0]
    buckets = [("strongBuy", "Strong buy", p["good"]),
               ("buy", "Buy", p["series"][2]),
               ("hold", "Hold", p["series"][3]),
               ("sell", "Sell", p["series"][1]),
               ("strongSell", "Strong sell", p["bad"])]
    total = sum(int(cur.get(k) or 0) for k, _, _ in buckets)
    if not total:
        return None
    fig = go.Figure()
    for key, label, color in buckets:
        n = int(cur.get(key) or 0)
        if not n:
            continue
        fig.add_trace(go.Bar(
            x=[n], y=["ratings"], orientation="h", name=f"{label} ({n})",
            marker_color=color,
            hovertemplate=f"{label}: {n} of {total}<extra></extra>"))
    fig.update_layout(barmode="stack")
    charts.style(fig, height=130, legend=True, hovermode="closest")
    fig.update_yaxes(visible=False)
    fig.update_xaxes(visible=False)
    return fig


def render(ticker: str, r: pd.Series, cik_str: str | None = None) -> None:
    """La scheda intera."""
    p = charts.palette()
    prof = fetch_profile(ticker)

    # ---------------- CHI E' ----------------
    st.markdown("#### What this company does")
    if prof is None:
        st.info(
            "The market data provider has no profile for this symbol right "
            "now — either the connection failed or the ticker has changed. "
            "The financial statements elsewhere in the app come from SEC EDGAR "
            "via the company's CIK and are unaffected.", icon="📡")
    else:
        # PRIMA I FATTI, POI IL TESTO.
        #
        # La descrizione depositata e' un paragrafo unico di quindici righe,
        # scritto dagli avvocati della societa' per essere completo e non per
        # essere letto. Messo per primo era un muro: si scorreva senza leggerlo
        # e si arrivava ai riquadri, che sono la parte che si guarda davvero.
        # Ora i sei fatti stanno in cima, la prima frase — che e' quella che
        # dice il mestiere — resta visibile, e il resto si apre a richiesta.
        facts = [
            ("Sector", prof.get("sector") or r.get("sector")),
            ("Industry", prof.get("industry") or r.get("industry")),
            ("Headquarters", ", ".join(
                [x for x in (prof.get("city"), prof.get("state"),
                             prof.get("country")) if x]) or None),
            ("Employees", f"{int(prof['fullTimeEmployees']):,}"
             if prof.get("fullTimeEmployees") else None),
            ("Listed on", prof.get("exchange") or r.get("exchange")),
            ("SIC classification", f"{r.get('sic')} — {r.get('sic_description')}"
             if pd.notna(r.get("sic")) else None),
        ]
        shown = [f for f in facts if f[1]]
        cols = st.columns(3)
        for i, (label, value) in enumerate(shown):
            cols[i % 3].markdown(
                f"<span style='color:{p['muted']};font-size:0.85rem'>{label}"
                f"</span><br><span style='font-size:1.0rem'>{value}</span>"
                f"<br>&nbsp;", unsafe_allow_html=True)

        summary = prof.get("longBusinessSummary")
        if summary:
            head, rest = _split_summary(summary)
            st.markdown(f"**{head}**")
            if rest:
                with st.expander("Read the full business description",
                                 expanded=False):
                    st.markdown(rest)
                    st.caption(
                        "The company's own description, as filed with the "
                        "provider — the one place on this page where products "
                        "and markets are named in its own words. The chart "
                        "below turns them into figures.")
        if prof.get("website"):
            st.markdown(f"🔗 [{prof['website']}]({prof['website']})")

    # ---------------- DA DOVE ARRIVANO I RICAVI ----------------
    #
    # SE NON C'E' IL DATO, NON C'E' LA SEZIONE. Prima qui stava una nota fissa
    # che spiegava perche' il dato mancava: compariva identica su ogni titolo,
    # non diceva nulla di quella societa' e occupava il posto di
    # un'informazione. Una spiegazione ripetuta novecento volte non e' una
    # spiegazione, e' rumore.
    if HAS_SEGMENTS and cik_str:
        _render_segments(ticker, cik_str)

    # ---------------- COSA SI ASPETTANO GLI ANALISTI ----------------
    st.divider()
    st.markdown("#### What analysts expect")
    a = fetch_analysts(ticker)
    if a is None:
        st.info("No analyst coverage available for this symbol from the "
                "provider.", icon="📡")
    else:
        cur, mean = a.get("current"), a.get("mean")
        upside = ((mean / cur - 1) * 100) if (cur and mean) else None
        k = st.columns(4)
        k[0].metric("Price today", f"${cur:,.2f}" if cur else "—")
        k[1].metric("Mean target", f"${mean:,.2f}" if mean else "—",
                    delta=f"{upside:+.0f}% implied" if upside is not None else None,
                    delta_color="normal" if (upside or 0) >= 0 else "inverse",
                    help="**What it is** — the average of the price targets "
                         "published by the analysts covering the stock.\n\n"
                         "**How to read it** — an opinion about the next twelve "
                         "months, not a filed figure. Targets cluster around "
                         "the current price and move after it more often than "
                         "before it: treat the spread as more informative than "
                         "the average.")
        k[2].metric("Target range",
                    f"${a['low']:,.0f} – ${a['high']:,.0f}"
                    if (a.get("low") and a.get("high")) else "—",
                    help="**How to read it** — a wide range means the analysts "
                         "disagree about the business, which is itself useful "
                         "information. A narrow one means consensus, not "
                         "certainty.")
        k[3].metric("Analysts covering",
                    str(int(a["n_analysts"])) if a.get("n_analysts") else "—",
                    delta=str(a.get("recommendation") or "").replace("_", " ")
                    or None, delta_color="off",
                    help="How many analysts publish a target. Thin coverage — "
                         "under five — means the consensus is a handful of "
                         "people, not a market view.")

        fig = target_chart(a)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True,
                            key=f"ov_targets_{ticker}")
            st.caption(
                "Blue is where the stock trades today; the band is the full "
                "range of published targets. **This is the only forward-"
                "looking material in the whole app** — everything else is "
                "either filed with the SEC or computed from what was filed.")

        bar = _rating_bar(a.get("recs") or [], p)
        if bar is not None:
            st.markdown("##### How they rate it, this month")
            st.plotly_chart(bar, use_container_width=True,
                            key=f"ov_ratings_{ticker}")

        if a.get("grades"):
            with st.expander("Recent rating changes", expanded=False):
                g = pd.DataFrame(a["grades"])
                keep = [c for c in ("GradeDate", "Firm", "FromGrade", "ToGrade",
                                    "Action", "priorPriceTarget", "priceTarget")
                        if c in g.columns]
                g = g[keep].rename(columns={
                    "GradeDate": "Date", "FromGrade": "From", "ToGrade": "To",
                    "priorPriceTarget": "Prior target",
                    "priceTarget": "New target"})
                if "Date" in g.columns:
                    g["Date"] = pd.to_datetime(g["Date"], errors="coerce"
                                               ).dt.strftime("%b %d, %Y")
                st.dataframe(g, use_container_width=True, hide_index=True)
                st.caption("Upgrades and downgrades are worth more than the "
                           "level of a rating: the change is the new "
                           "information.")

    # ---------------- NOTIZIE ----------------
    st.divider()
    st.markdown("#### Recent news")
    news = fetch_news(ticker)
    if not news:
        st.info("No recent news for this symbol from the provider.", icon="📡")
        return
    df = pd.DataFrame(news)
    st.dataframe(
        df[["date", "source", "title", "url"]].rename(columns={
            "date": "Date", "source": "Source", "title": "Headline",
            "url": "Link"}),
        use_container_width=True, hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("Read", display_text="open ↗"),
            "Headline": st.column_config.Column(width="large"),
        })
    st.caption(
        "Headlines from the market data provider's feed, most recent first. "
        "They are **not** filtered for relevance: a story about a competitor "
        "or about the sector can appear here, and the app has no way to tell "
        "the difference. Nothing on this page feeds any calculation elsewhere "
        "in the app.")
