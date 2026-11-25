"""Plotly-Kurschart mit News-, Insider- und Elliott-Wellen-Overlay."""
import bisect
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit.components.v1 as components

from dashboard.insider_data import SOURCE_LABEL, TRADE_TYPE_ICON, fmt_amount
from dashboard.theme import (
    SENTIMENT_COLOR,
    SENTIMENT_ICON,
    WAVE_DIRECTION_COLOR,
    hex_to_rgba,
)

# ─── Nearest trading-day helper ──────────────────────────────────────────────

def _nearest_price_date(target: str, sorted_dates: list[str]) -> str | None:
    """Map any date to the nearest preceding trading day with price data."""
    if not sorted_dates:
        return None
    if target in sorted_dates:
        return target
    idx = bisect.bisect_right(sorted_dates, target)
    if idx > 0:
        return sorted_dates[idx - 1]
    return sorted_dates[0]


# ─── Chart builder ───────────────────────────────────────────────────────────

def build_price_chart(
    prices:  pd.DataFrame,
    news:    pd.DataFrame,
    obs:     pd.DataFrame,
    ticker:  str,
    chart_type: str,
    insider: pd.DataFrame | None = None,
    elliott: list[dict] | None = None,
    current_wave: dict | None = None,
) -> go.Figure:
    fig = go.Figure()

    if prices.empty:
        fig.add_annotation(
            text="Keine Kursdaten verfügbar (Ticker evtl. ungültig oder Börse geschlossen)",
            showarrow=False, font=dict(size=18, color="#888"),
            xref="paper", yref="paper", x=0.5, y=0.5,
        )
        fig.update_layout(template="plotly_dark", height=540,
                          paper_bgcolor="rgba(0,0,0,0)")
        return fig

    sorted_dates = sorted(prices["date"].tolist())
    price_high   = prices.set_index("date")["high"].to_dict()
    price_low    = prices.set_index("date")["low"].to_dict()

    # ── Preis-Trace ──────────────────────────────────────────────────────────
    if chart_type == "Candlestick":
        fig.add_trace(go.Candlestick(
            x=prices["date"],
            open=prices["open"], high=prices["high"],
            low=prices["low"],   close=prices["close"],
            name="Kurs",
            increasing_line_color="#00C853",  decreasing_line_color="#D50000",
            increasing_fillcolor="#00C853",   decreasing_fillcolor="#D50000",
            whiskerwidth=0.3,
        ))
    else:
        fig.add_trace(go.Scatter(
            x=prices["date"], y=prices["close"],
            mode="lines", name="Schlusskurs",
            line=dict(color="#2979FF", width=2),
            fill="tozeroy",
            fillcolor="rgba(41,121,255,0.07)",
        ))

    # ── Volumen ──────────────────────────────────────────────────────────────
    vol_colors = [
        "#00C853" if c >= o else "#D50000"
        for c, o in zip(prices["close"], prices["open"])
    ]
    fig.add_trace(go.Bar(
        x=prices["date"], y=prices["volume"],
        name="Volumen", marker_color=vol_colors,
        opacity=0.22, yaxis="y2",
    ))

    # ── News-Marker ───────────────────────────────────────────────────────────
    if not news.empty:
        m_x, m_y, m_color, m_text, m_size, m_url = [], [], [], [], [], []

        for date, grp in news.groupby("date"):
            mapped = _nearest_price_date(date, sorted_dates)
            if mapped is None:
                continue
            y_high = price_high[mapped]

            grp_sorted = grp.sort_values("sentiment_score", ascending=False)
            parts, sentiments = [], []
            for _, row in grp_sorted.iterrows():
                icon    = SENTIMENT_ICON.get(row["sentiment_label"], "⚪")
                summary = str(row["summary"] or "")[:220]
                parts.append(
                    f"<b>{icon} {row['source']}</b>  ·  <i>{date}</i><br>"
                    f"<b>{row['title'][:80]}{'…' if len(row['title'])>80 else ''}</b><br>"
                    f"<span style='color:#ccc;font-size:11px'>{summary}</span><br>"
                    f"Sentiment: {row['sentiment_label'].upper()} "
                    f"({row['sentiment_score']:+.2f})"
                )
                sentiments.append(row["sentiment_label"])

            dominant  = Counter(sentiments).most_common(1)[0][0]
            top_url   = str(grp_sorted.iloc[0]["url"] or "")
            click_hint = "<br><br><i>🖱️ Klicken zum Öffnen des Artikels</i>" if top_url else ""
            hover    = (
                f"<b>📰 {len(grp)} Artikel  |  {date}</b>"
                + "<br>──────────────────────<br>"
                + "<br>──────────────────────<br>".join(parts)
                + click_hint
            )
            m_x.append(mapped)
            m_y.append(y_high * 1.018)
            m_color.append(SENTIMENT_COLOR[dominant])
            m_text.append(hover)
            m_size.append(min(14 + len(grp) * 2, 22))
            m_url.append(top_url)

        if m_x:
            fig.add_trace(go.Scatter(
                x=m_x, y=m_y,
                mode="markers",
                marker=dict(
                    symbol="triangle-down",
                    size=m_size,
                    color=m_color,
                    line=dict(width=1.5, color="white"),
                    opacity=0.95,
                ),
                text=m_text,
                customdata=m_url,
                hovertemplate="%{text}<extra></extra>",
                name="📰 News",
            ))

    # ── Insider-Trade-Marker (Congress + ARK/Cathie Wood + Trump-Familie) ────
    # Einheitliches gelbes Info-Zeichen (ℹ) statt Kauf/Verkauf-Einfärbung —
    # Kauf/Verkauf-Richtung steht weiterhin im Hover-Text pro Trade.
    if insider is not None and not insider.empty:
        i_x, i_y, i_text, i_size = [], [], [], []

        for date, grp in insider.groupby("trade_date"):
            mapped = _nearest_price_date(str(date), sorted_dates)
            if mapped is None:
                continue
            y_low = price_low[mapped]

            parts = []
            for _, row in grp.iterrows():
                icon   = TRADE_TYPE_ICON.get(row["trade_type"], "⚪")
                origin = SOURCE_LABEL.get(row["source"], row["source"])
                if row["shares_delta"] not in (None, 0) and not pd.isna(row["shares_delta"]):
                    detail = f"{abs(row['shares_delta']):,.0f} Aktien"
                else:
                    detail = fmt_amount(row["amount_min"], row["amount_max"])
                parts.append(
                    f"{icon} <b>{row['trader']}</b>  ·  <i>{origin}</i><br>"
                    f"<span style='color:#ccc;font-size:11px'>"
                    f"{row['trade_type'].upper()} · {detail}</span>"
                )

            hover = (
                f"<b>ℹ️ {len(grp)} Insider-Trade(s)  |  {date}</b>"
                + "<br>──────────────────────<br>"
                + "<br>──────────────────────<br>".join(parts)
            )
            i_x.append(mapped)
            i_y.append(y_low * 0.991)
            i_text.append(hover)
            i_size.append(min(16 + len(grp) * 2, 24))

        if i_x:
            fig.add_trace(go.Scatter(
                x=i_x, y=i_y,
                mode="markers+text",
                marker=dict(
                    symbol="circle",
                    size=i_size,
                    color="#FFD600",
                    line=dict(width=1.5, color="white"),
                    opacity=0.95,
                ),
                text=["ℹ"] * len(i_x),
                textfont=dict(color="#1a1a1a", size=12, family="Arial Black"),
                textposition="middle center",
                customdata=i_text,
                hovertemplate="%{customdata}<extra></extra>",
                name="🏛️ Insider",
            ))

    # ── Beobachtungs-Marker ───────────────────────────────────────────────────
    if not obs.empty:
        o_x, o_y, o_color, o_text = [], [], [], []
        for _, row in obs.iterrows():
            mapped = _nearest_price_date(str(row["obs_date"]), sorted_dates)
            if mapped is None:
                continue
            y_low = price_low[mapped] * 0.982
            o_x.append(mapped)
            o_y.append(y_low)
            o_color.append(row["color"] or "#FFD600")
            o_text.append(
                f"<b>📌 Beobachtung  |  {row['obs_date']}</b><br>"
                f"{row['note']}<br>"
                f"<i>Erstellt: {str(row['created_at'])[:10]}</i>"
            )

        if o_x:
            fig.add_trace(go.Scatter(
                x=o_x, y=o_y,
                mode="markers",
                marker=dict(
                    symbol="star",
                    size=16,
                    color=o_color,
                    line=dict(width=1.5, color="white"),
                    opacity=1.0,
                ),
                text=o_text,
                hovertemplate="%{text}<extra></extra>",
                name="📌 Beobachtungen",
            ))

    # ── Elliott-Wellen-Overlay ───────────────────────────────────────────────
    # Farbe = Richtung (bullisch/bärisch), Linienstil = Wellentyp (solid=Impuls,
    # dash=Korrektur) — so lassen sich Trendrichtung und Struktur unabhängig
    # voneinander ablesen.
    if elliott:
        for n, pattern in enumerate(elliott):
            pts   = pattern["points"]
            color = WAVE_DIRECTION_COLOR.get(pattern["direction"], "#6FD9A0")
            dash  = "solid" if pattern["type"] == "impulse" else "dash"
            dir_label = "bullisch ↑" if pattern["direction"] == "bullish" else "bärisch ↓"
            fig.add_trace(go.Scatter(
                x=[pt["date"] for pt in pts],
                y=[pt["price"] for pt in pts],
                mode="lines+markers+text",
                text=[pt["label"] for pt in pts],
                textposition="top center",
                textfont=dict(size=13, color=color),
                line=dict(color=color, width=1.5, dash=dash),
                marker=dict(size=6, color=color),
                name=f"🌊 {'Impuls' if pattern['type']=='impulse' else 'Korrektur'} "
                     f"({dir_label}, {pattern['confidence']*100:.0f}%)",
                hovertemplate=(
                    f"<b>Welle %{{text}}</b><br>%{{x}} · %{{y:.2f}}<br>"
                    f"Konfidenz: {pattern['confidence']*100:.0f}%<extra></extra>"
                ),
                showlegend=True,
                legendgroup=f"elliott{n}",
            ))

    # ── Aktuell laufende Welle + Ausblick (noch unbestätigt) ──────────────────
    # Historie bleibt kräftig/solid; alles, was in die Zukunft zeigt (Ziellinie
    # + Zielzone), wird bewusst blasser/transparenter gerendert, damit auf den
    # ersten Blick klar ist: das ist eine Prognose, keine Kursdaten.
    if current_wave:
        from src.analytics.elliott_wave import STATE_LABELS
        cw = current_wave
        cw_color = WAVE_DIRECTION_COLOR.get(cw["direction"], "#6FD9A0")
        cw_label = STATE_LABELS.get(cw["state"], cw["state"])

        # Bereits bestätigte Pivots der laufenden Struktur (z.B. 0/1/2 bei
        # "Welle 3 in Bildung") — dieselbe Darstellung wie abgeschlossene
        # Elliott-Muster, damit auf einen Blick klar ist, worauf sich die
        # Prognose stützt.
        cw_points = cw.get("points") or []
        if cw_points:
            fig.add_trace(go.Scatter(
                x=[pt["date"] for pt in cw_points],
                y=[pt["price"] for pt in cw_points],
                mode="lines+markers+text",
                text=[pt["label"] for pt in cw_points],
                textposition="top center",
                textfont=dict(size=13, color=cw_color),
                line=dict(color=cw_color, width=1.5),
                marker=dict(size=6, color=cw_color),
                name=f"🌊 Bestätigt: {cw_label}",
                hovertemplate=(
                    "<b>Welle %{text}</b><br>%{x} · %{y:.2f}<extra></extra>"
                ),
                showlegend=True,
                legendgroup="current_wave",
            ))

        # Laufendes Wellenbein: letzter bestätigter Pivot -> aktueller Kurs
        fig.add_trace(go.Scatter(
            x=[cw["last_pivot_date"], cw["current_date"]],
            y=[cw["last_pivot_price"], cw["current_price"]],
            mode="lines+markers",
            line=dict(color=cw_color, width=2, dash="dot"),
            marker=dict(size=7, color=cw_color, symbol="circle"),
            name=f"🎯 {cw_label} ({cw['confidence']*100:.0f}%)",
            hovertemplate=(
                f"<b>{cw_label}</b><br>{cw['expectation_text']}<extra></extra>"
            ),
            showlegend=True,
            legendgroup="current_wave",
        ))

        # Trennlinie "Heute" — markiert den Übergang von Kursdaten zur Prognose.
        # add_shape/add_annotation statt add_vline: add_vline berechnet die
        # Default-Annotationsposition intern über einen Mittelwert der x-Achse,
        # was bei String-Datumsachsen (kein numerischer/datetime dtype) crasht.
        fig.add_shape(
            type="line", xref="x", yref="paper",
            x0=cw["current_date"], x1=cw["current_date"], y0=0, y1=1,
            line=dict(color="rgba(255,255,255,0.25)", width=1, dash="dot"),
        )
        fig.add_annotation(
            x=cw["current_date"], y=1, xref="x", yref="paper",
            text="Heute", showarrow=False, yanchor="bottom",
            font=dict(size=10, color="rgba(255,255,255,0.5)"),
        )

        # Projizierter Ausblick: Ziellinie + Zielzone rechts vom aktuellen Kurs,
        # abgeleitet aus der Elliott-Wellenberechnung (Fibonacci-Ziel des
        # laufenden Wellenbeins) — blasse/transparente Darstellung, da Prognose.
        if cw["target_low"] is not None:
            d0     = datetime.strptime(sorted_dates[0],  "%Y-%m-%d")
            d_last = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
            span_days = max((d_last - d0).days, 1)
            extend    = max(5, int(span_days * 0.06))
            future_date = (
                datetime.strptime(cw["current_date"], "%Y-%m-%d") + timedelta(days=extend)
            ).strftime("%Y-%m-%d")
            target_mid = (cw["target_low"] + cw["target_high"]) / 2
            zone_fill  = hex_to_rgba(cw_color, 0.12)

            # Ziellinie (Ausblick): aktueller Kurs -> Fibonacci-Zielmitte
            fig.add_trace(go.Scatter(
                x=[cw["current_date"], future_date],
                y=[cw["current_price"], target_mid],
                mode="lines",
                line=dict(color=hex_to_rgba(cw_color, 0.55), width=1.5, dash="dashdot"),
                name="🔮 Ausblick (Elliott-Projektion)",
                hovertemplate=(
                    f"<b>Prognose</b><br>Zielmitte ≈ {target_mid:.2f}<extra></extra>"
                ),
                showlegend=True,
                legendgroup="current_wave",
            ))

            fig.add_trace(go.Scatter(
                x=[cw["current_date"], future_date, future_date, cw["current_date"]],
                y=[cw["target_low"], cw["target_low"], cw["target_high"], cw["target_high"]],
                mode="lines",
                line=dict(width=0),
                fill="toself",
                fillcolor=zone_fill,
                name=f"🎯 Zielzone {cw['target_low']:.2f}–{cw['target_high']:.2f}",
                hovertemplate=(
                    f"<b>Projizierte Zielzone</b><br>{cw['target_low']:.2f} – {cw['target_high']:.2f}"
                    "<extra></extra>"
                ),
                showlegend=True,
                legendgroup="current_wave",
            ))

            # Weitere Wellenbeine derselben Struktur (z.B. 4+5 nach Welle 3,
            # oder A+B+C nach Welle 5) — zeitlich weiter verkettet, mit
            # abnehmender Deckkraft je weiter in der Zukunft (= spekulativer,
            # da auf einer noch unbestätigten Zielzone aufbauend statt auf
            # echten Kursdaten).
            prev_date, prev_price = future_date, target_mid
            for step, pw in enumerate(cw.get("projected_waves") or [], start=2):
                pw_future_date = (
                    datetime.strptime(cw["current_date"], "%Y-%m-%d")
                    + timedelta(days=extend * step)
                ).strftime("%Y-%m-%d")
                pw_mid    = (pw["target_low"] + pw["target_high"]) / 2
                line_fade = max(0.15, 0.55 - 0.15 * (step - 1))
                zone_fade = max(0.03, 0.12 - 0.03 * (step - 1))

                fig.add_trace(go.Scatter(
                    x=[prev_date, pw_future_date],
                    y=[prev_price, pw_mid],
                    mode="lines",
                    line=dict(color=hex_to_rgba(cw_color, line_fade), width=1.2, dash="dashdot"),
                    name=f"🔮 Welle {pw['label']} (Projektion)",
                    hovertemplate=(
                        f"<b>Welle {pw['label']}</b><br>{pw['note']}<br>"
                        f"Zielzone ≈ {pw['target_low']:.2f}–{pw['target_high']:.2f}<extra></extra>"
                    ),
                    showlegend=True,
                    legendgroup="current_wave",
                ))
                fig.add_trace(go.Scatter(
                    x=[prev_date, pw_future_date, pw_future_date, prev_date],
                    y=[pw["target_low"], pw["target_low"], pw["target_high"], pw["target_high"]],
                    mode="lines",
                    line=dict(width=0),
                    fill="toself",
                    fillcolor=hex_to_rgba(cw_color, zone_fade),
                    name=f"🎯 Welle {pw['label']} Zielzone {pw['target_low']:.2f}–{pw['target_high']:.2f}",
                    hovertemplate=(
                        f"<b>Welle {pw['label']} — Projizierte Zielzone</b><br>"
                        f"{pw['target_low']:.2f} – {pw['target_high']:.2f}<extra></extra>"
                    ),
                    showlegend=True,
                    legendgroup="current_wave",
                ))
                fig.add_annotation(
                    x=pw_future_date, y=pw["target_high"], xref="x", yref="y",
                    text=pw["label"], showarrow=False, yanchor="bottom",
                    font=dict(size=11, color=hex_to_rgba(cw_color, min(1.0, line_fade + 0.3))),
                )
                prev_date, prev_price = pw_future_date, pw_mid

    # ── Layout ────────────────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(text=f"<b>{ticker}</b>", font=dict(size=20, color="#eee")),
        height=560,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(10,12,20,1)",
        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.05)",
            rangeslider_visible=False,
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.06)",
            side="right",
            title="Kurs",
        ),
        yaxis2=dict(
            overlaying="y", side="left",
            showgrid=False, showticklabels=False,
            range=[0, prices["volume"].max() * 7],
        ),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0.4)",
            font=dict(size=11),
        ),
        hovermode="closest",
        margin=dict(l=10, r=70, t=65, b=20),
        # list(fig.layout.annotations) statt [...]: sonst überschreibt dieser
        # Call die zuvor per add_annotation gesetzte "Heute"-Markierung, da
        # update_layout(annotations=[...]) die komplette Liste ersetzt statt
        # zu ergänzen.
        annotations=list(fig.layout.annotations) + [dict(
            text=(
                "▼ <span style='color:#00C853'>Pos. News</span>  "
                "▼ <span style='color:#D50000'>Neg. News</span>  "
                "▼ <span style='color:#2979FF'>Neutr. News</span>  "
                "◆ <span style='color:#00C853'>Insider-Kauf</span>  "
                "◆ <span style='color:#D50000'>Insider-Verkauf</span>  "
                "★ Beobachtung"
                + (
                    "&nbsp;&nbsp;·&nbsp;&nbsp;"
                    f"— <span style='color:{WAVE_DIRECTION_COLOR['bullish']}'>bullische Welle</span>  "
                    f"— <span style='color:{WAVE_DIRECTION_COLOR['bearish']}'>bärische Welle</span>  "
                    "(solid=Impuls, gestrichelt=Korrektur, gepunktet=Ausblick)"
                    if (elliott or current_wave) else ""
                )
            ),
            xref="paper", yref="paper", x=0.01, y=1.055,
            showarrow=False, font=dict(size=11), align="left",
        )],
    )
    return fig


# ─── Chart-Rendering mit Klick-auf-News-Flag ────────────────────────────────

def render_chart_with_news_link(fig: go.Figure, height: int = 560) -> None:
    """Rendert den Plotly-Chart und öffnet beim Klick auf einen News-Marker
    den zugehörigen Original-Artikel in einem neuen Tab."""
    chart_html = fig.to_html(include_plotlyjs="cdn", full_html=False, div_id="price-chart-div")
    click_handler = """
    <script>
    (function () {
        var gd = document.getElementById("price-chart-div");
        if (!gd) return;
        gd.on("plotly_click", function (evt) {
            var pt = evt.points && evt.points[0];
            if (!pt) return;
            if (pt.data.name === "📰 News" && pt.customdata) {
                window.open(pt.customdata, "_blank");
            }
        });
    })();
    </script>
    """
    components.html(chart_html + click_handler, height=height + 40, scrolling=False)
