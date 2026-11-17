"""
Regelbasierte Erkennung von Elliott-Wellen-Strukturen in Kursdaten.

Ablauf: (1) Zigzag-Pivots aus High/Low extrahieren (Schwellenwert-basiert,
filtert Rauschen). (2) Jedes Fenster von 6 aufeinanderfolgenden Pivots gegen
die drei harten Elliott-Regeln für eine 5-Wellen-Impulsbewegung prüfen.
(3) Jedes Fenster von 4 Pivots gegen eine einfache ABC-Korrekturstruktur
prüfen. (4) Nicht-überlappende Treffer nach Konfidenz (Fibonacci-Konformität)
auswählen.

Kein ML, keine Kursvorhersage — reine Struktur-Mustererkennung auf
historischen Daten, wie sie ein Chartist von Hand einzeichnen würde.
"""
from __future__ import annotations

import pandas as pd

FIB_RETRACE = (0.236, 0.382, 0.5, 0.618, 0.786)
FIB_EXTEND  = (0.618, 1.0, 1.272, 1.618, 2.618)

# Für "Welle 3 in Bildung": statt zum aktuellen (bereits über Welle 1
# ausgebrochenen) Kurs einzusteigen, wird eine Limit-Order auf ein
# Retracement der 0-1-Bewegung platziert - nahe dem 38,2%-Fib-Level, aber
# bewusst etwas darunter gewählt. Rationale: Welle 2 darf laut harter
# Elliott-Regel nie unter den Start von Welle 1 (Punkt 0) zurücklaufen -
# das schützt aber nur die bereits abgeschlossene Welle 2, nicht automatisch
# eine neue Order auf diesem Niveau. Die Order ergibt nur Sinn, wenn dieses
# Level oberhalb des bereits bestätigten Welle-2-Tiefs liegt (siehe unten,
# valid_entry-Check) - sonst läge der Einstieg auf/unter dem strukturellen
# Stop und das Setup hätte keinen Puffer mehr.
WAVE3_ENTRY_RETRACE_PCT = 0.35


def _zigzag(dates: list, highs: list, lows: list, threshold_pct: float) -> list[dict]:
    """Extrahiert alternierende Hoch-/Tiefpunkte, deren Umkehrung mindestens
    threshold_pct % beträgt. Kleinere Schwankungen werden als Rauschen
    ignoriert, damit nur strukturell relevante Schwenks übrig bleiben."""
    n = len(dates)
    if n < 3:
        return []

    thresh = threshold_pct / 100.0

    # Anfangsrichtung bestimmen: erste Bewegung, die die Schwelle überschreitet
    direction = 0
    i = 1
    while i < n and direction == 0:
        if highs[i] >= highs[0] * (1 + thresh):
            direction = 1
        elif lows[i] <= lows[0] * (1 - thresh):
            direction = -1
        i += 1
    if direction == 0:
        return []

    start_type = "low" if direction == 1 else "high"
    start_price = lows[0] if direction == 1 else highs[0]
    pivots = [{"idx": 0, "date": dates[0], "price": start_price, "type": start_type}]

    extreme_idx, extreme_price = 0, start_price

    for i in range(1, n):
        if direction == 1:
            if highs[i] > extreme_price:
                extreme_price, extreme_idx = highs[i], i
            if lows[i] <= extreme_price * (1 - thresh):
                pivots.append({"idx": extreme_idx, "date": dates[extreme_idx],
                                "price": extreme_price, "type": "high"})
                direction = -1
                extreme_price, extreme_idx = lows[i], i
        else:
            if lows[i] < extreme_price:
                extreme_price, extreme_idx = lows[i], i
            if highs[i] >= extreme_price * (1 + thresh):
                pivots.append({"idx": extreme_idx, "date": dates[extreme_idx],
                                "price": extreme_price, "type": "low"})
                direction = 1
                extreme_price, extreme_idx = highs[i], i

    pivots.append({"idx": extreme_idx, "date": dates[extreme_idx], "price": extreme_price,
                    "type": "high" if direction == 1 else "low"})
    return pivots


def _fib_closeness(ratio: float, targets: tuple[float, ...]) -> float:
    """1.0 = exakt auf einem Fibonacci-Level, fällt linear ab, 0 ab 25% Abweichung."""
    nearest = min(targets, key=lambda t: abs(ratio - t))
    dist = abs(ratio - nearest) / nearest
    return max(0.0, 1.0 - dist / 0.25)


def _check_impulse(p: list[dict], bullish: bool) -> dict | None:
    """Prüft ein 6-Punkte-Fenster [0,1,2,3,4,5] gegen die drei unverhandelbaren
    Elliott-Regeln für eine 5-Wellen-Impulsbewegung. Gibt bei Verstoß None
    zurück (kein erzwungenes Fitting ungültiger Strukturen)."""
    v = [pt["price"] for pt in p]
    if not bullish:
        v = [-x for x in v]

    len1, len3, len5 = v[1] - v[0], v[3] - v[2], v[5] - v[4]
    if len1 <= 0 or len3 <= 0 or len5 <= 0:
        return None

    # Regel 1: Welle 2 retraced nie über den Start von Welle 1 hinaus
    if v[2] <= v[0]:
        return None
    # Regel 2: Welle 3 ist nie die kürzeste der Impulswellen 1/3/5
    if len3 < len1 and len3 < len5:
        return None
    # Regel 3: Welle 4 überschneidet nicht das Kursgebiet von Welle 1
    if v[4] <= v[1]:
        return None
    # Zusatz: Welle 3 macht ein neues Hoch über Welle 1, Welle 5 über Welle 3
    if v[3] <= v[1] or v[5] <= v[3]:
        return None

    w2_retrace = (v[1] - v[2]) / len1
    w4_retrace = (v[3] - v[4]) / len3
    w3_ext     = len3 / len1
    w5_ext     = len5 / len1

    confidence = (
        _fib_closeness(w2_retrace, FIB_RETRACE)
        + _fib_closeness(w4_retrace, FIB_RETRACE)
        + _fib_closeness(w3_ext, FIB_EXTEND)
        + _fib_closeness(w5_ext, FIB_EXTEND)
    ) / 4.0

    return {
        "type": "impulse",
        "direction": "bullish" if bullish else "bearish",
        "points": [{"date": pt["date"], "price": pt["price"], "label": str(i)}
                   for i, pt in enumerate(p)],
        "confidence": round(confidence, 3),
        "idx_range": (p[0]["idx"], p[-1]["idx"]),
    }


def _check_corrective(p: list[dict], bullish_prior: bool) -> dict | None:
    """Prüft ein 4-Punkte-Fenster [0,A,B,C] gegen eine einfache Zigzag-Korrektur
    (A-B-C), wie sie typischerweise auf einen Impuls folgt."""
    v = [pt["price"] for pt in p]
    if not bullish_prior:
        v = [-x for x in v]

    len_a = v[0] - v[1]
    if len_a <= 0:
        return None
    # B retraced einen Teil von A, überschreitet aber nicht den Start von A
    if v[2] >= v[0] or v[2] <= v[1]:
        return None
    # C liegt unter dem Tief von A (typische Zigzag-Korrektur, kein Flat)
    if v[3] >= v[1]:
        return None

    b_retrace = (v[2] - v[1]) / len_a
    len_c = v[2] - v[3]
    c_ext = len_c / len_a

    confidence = (
        _fib_closeness(b_retrace, FIB_RETRACE)
        + _fib_closeness(c_ext, FIB_EXTEND)
    ) / 2.0

    labels = ["0", "A", "B", "C"]
    return {
        "type": "corrective",
        "direction": "bearish" if bullish_prior else "bullish",
        "points": [{"date": pt["date"], "price": pt["price"], "label": lbl}
                   for pt, lbl in zip(p, labels)],
        "confidence": round(confidence, 3),
        "idx_range": (p[0]["idx"], p[-1]["idx"]),
    }


STATE_LABELS = {
    "wave3_forming":       "Welle 3 in Bildung",
    "wave5_forming":       "Welle 5 in Bildung",
    "correction_forming":  "Korrektur (Welle A) nach Impuls",
    "new_impulse_possible": "Möglicher neuer Impuls (Welle 1) nach Korrektur",
}

# Übergeordnete Struktur der laufenden Bewegung: ein Impuls zählt fünf Beine
# (1-2-3-4-5), eine Korrektur drei (A-B-C) — fachlich "Fünfteiler"/"Dreiteiler".
STRUCTURE_LABELS = {
    "impulse":   "5-Teiler (Impuls, Wellen 1-2-3-4-5)",
    "corrective": "3-Teiler (Korrektur, Wellen A-B-C)",
}


def _leg_zone(
    start_price: float, sign: float, ref_len: float, ratio_lo: float, ratio_hi: float,
) -> tuple[float, float, float]:
    """Projiziert ein einzelnes zukünftiges Wellenbein als Fibonacci-Zielzone
    ab start_price. sign=+1 bewegt sich in der zuvor als 'up' kanonisierten
    Richtung, sign=-1 dagegen (Retracement). Gibt (target_low, target_high,
    Zielmitte) zurück — die Zielmitte dient als Ankerpunkt für das jeweils
    nächste Wellenbein in der Kette."""
    t1 = start_price + sign * ratio_lo * ref_len
    t2 = start_price + sign * ratio_hi * ref_len
    lo, hi = min(t1, t2), max(t1, t2)
    return lo, hi, (lo + hi) / 2


def analyze_current_wave(prices: pd.DataFrame, threshold_pct: float = 5.0) -> dict | None:
    """Prüft, ob sich der Kurs aktuell (letzter verfügbarer Datenpunkt) in einer
    noch nicht abgeschlossenen Elliott-Wellen-Bewegung befindet, und leitet
    daraus eine Zielzone für das laufende Wellenbein ab.

    Da eine absolute, eindeutige Wellenzählung ab einem beliebigen historischen
    Startpunkt fachlich immer subjektiv ist (das gilt auch für menschliche
    Chartisten), beschränkt sich die Analyse bewusst auf die letzten paar
    Pivots — also auf die Frage "wie geht die zuletzt etablierte Bewegung
    plausibel weiter", nicht auf eine Zählung des gesamten Kurshistorie.

    Prüfreihenfolge (erste zutreffende Interpretation gewinnt, spezifischste
    zuerst): (C) Impuls gerade abgeschlossen → Korrektur läuft an;
    (B) Wellen 1–4 bestätigt → Welle 5 läuft; (A) Wellen 1–2 bestätigt →
    Welle 3 läuft; (D) ABC-Korrektur gerade abgeschlossen → evtl. neuer
    Impuls. Gibt None zurück, wenn keine dieser Situationen (mit
    Mindest-Konfidenz) vorliegt.

    Rückgabe: dict mit state, direction, confidence, target_low/high,
    expectation_text, current_price, last_pivot_date/price, points
    (die bereits bestätigten Pivots der laufenden Struktur, nummeriert wie
    im Elliott-Schema — z.B. 0/1/2 bei "Welle 3 in Bildung"), structure_type
    ("impulse" = Fünfteiler 1-2-3-4-5, "corrective" = Dreiteiler A-B-C) sowie
    projected_waves (verkettete Fibonacci-Projektion der noch ausstehenden
    Wellenbeine derselben Struktur, je weiter in der Zukunft desto
    spekulativer — leere Liste, wenn dafür noch keine belastbare
    Referenzlänge existiert) — oder None.
    """
    if prices.empty or len(prices) < 6:
        return None

    prices = prices.sort_values("date").reset_index(drop=True)
    dates = prices["date"].tolist()
    highs = prices["high"].tolist()
    lows  = prices["low"].tolist()
    current_price = float(prices["close"].iloc[-1])
    current_date  = dates[-1]

    raw_pivots = _zigzag(dates, highs, lows, threshold_pct)
    # Der letzte Eintrag von _zigzag ist immer der noch unbestätigte, laufende
    # Extremwert (kein echter Umkehrpunkt) — er markiert das aktuell laufende
    # Wellenbein und darf nicht als abgeschlossener Pivot in die Musterprüfung
    # einfließen, sonst gibt es kein "in Bildung" mehr zu erkennen.
    pivots = raw_pivots[:-1]
    if len(pivots) < 3:
        return None

    # "up" = das gerade laufende (unbestätigte) Wellenbein sollte steigen,
    # weil der letzte bestätigte Pivot ein Tief ist.
    up = pivots[-1]["type"] == "low"

    def flip(x: float) -> float:
        """Negation ist selbstinvers: dieselbe Funktion wandelt Preise nach
        'kanonisch bullish' um UND wandelt kanonische Zielwerte zurück."""
        return x if up else -x

    # (C) Impuls gerade abgeschlossen (letzter Pivot = Wellenende) -> Korrektur läuft an.
    # Die gerade beendete Impulsbewegung hat die entgegengesetzte Richtung von "up".
    if len(pivots) >= 6:
        window = pivots[-6:]
        hit = _check_impulse(window, bullish=(not up))
        # Bestätigung: Kurs muss bereits spürbar (>=15% des Impulses) in
        # Korrekturrichtung gelaufen sein — sonst könnte v5 auch einfach
        # noch eine (unbestätigte) Verschnaufpause im Impuls selbst sein.
        if hit and flip(current_price - window[-1]["price"]) >= 0.15 * abs(window[-1]["price"] - window[0]["price"]):
            v0, v5 = window[0]["price"], window[-1]["price"]
            full_len = abs(v5 - v0)
            t1 = v5 + flip(0.382 * full_len)
            t2 = v5 + flip(0.618 * full_len)
            target_low, target_high = min(t1, t2), max(t1, t2)
            target_mid = (target_low + target_high) / 2

            # Weiterprojektion innerhalb desselben Dreiteilers: Welle B retraced
            # einen Teil von Welle A, Welle C liegt typischerweise (Zigzag)
            # jenseits von Welle A. Referenzlänge für beide: die projizierte
            # Länge von Welle A selbst.
            sign  = 1.0 if up else -1.0
            a_len = abs(target_mid - v5)
            b_lo, b_hi, b_mid = _leg_zone(target_mid, -sign, a_len, 0.382, 0.618)
            c_lo, c_hi, _     = _leg_zone(b_mid, sign, a_len, 0.618, 1.618)

            return {
                "state": "correction_forming",
                "direction": "bullish" if up else "bearish",
                "confidence": hit["confidence"],
                "target_low": target_low, "target_high": target_high,
                "current_price": current_price, "current_date": current_date,
                "last_pivot_date": window[-1]["date"], "last_pivot_price": v5,
                "structure_type": "corrective",
                "points": [
                    {"date": pt["date"], "price": pt["price"], "label": str(i)}
                    for i, pt in enumerate(window)
                ],
                "projected_waves": [
                    {"label": "B", "target_low": b_lo, "target_high": b_hi,
                     "note": "Retracement von Welle A"},
                    {"label": "C", "target_low": c_lo, "target_high": c_hi,
                     "note": "Abschluss der Dreiteiler-Korrektur (Zigzag jenseits Welle A)"},
                ],
                "expectation_text": (
                    f"Impuls scheint bei {v5:.2f} ({window[-1]['date']}) abgeschlossen — "
                    f"Korrektur (Welle A) Richtung {target_low:.2f}–{target_high:.2f} erwartet. "
                    f"Danach: Welle B ({b_lo:.2f}–{b_hi:.2f}), Welle C ({c_lo:.2f}–{c_hi:.2f})."
                ),
            }

    # (B) Welle 5 in Bildung: Pivots 0,1,2,3,4 bestätigt (letzter = Wave-4-Ende)
    if len(pivots) >= 5:
        window = pivots[-5:]
        types = [p["type"] for p in window]
        expected = ["low", "high", "low", "high", "low"] if up else ["high", "low", "high", "low", "high"]
        if types == expected:
            v  = [p["price"] for p in window]
            vc = [flip(x) for x in v]
            len1, len3 = vc[1] - vc[0], vc[3] - vc[2]
            # Bestätigung: Kurs muss bereits über das Welle-3-Ende hinausgelaufen
            # sein — sonst wäre Welle 4 evtl. noch gar nicht wirklich fertig.
            confirmed_break = flip(current_price) > vc[3]
            if (confirmed_break and vc[1] > vc[0] and vc[2] > vc[0]
                    and vc[3] > vc[1] and vc[4] > vc[1] and len1 > 0 and len3 > 0):
                w2_retrace = (vc[1] - vc[2]) / len1
                w4_retrace = (vc[3] - vc[4]) / len3
                confidence = (_fib_closeness(w2_retrace, FIB_RETRACE) + _fib_closeness(w4_retrace, FIB_RETRACE)) / 2
                p4 = v[4]
                t1 = p4 + flip(0.618 * len1)
                t2 = p4 + flip(1.618 * len1)
                target_low, target_high = min(t1, t2), max(t1, t2)
                target_mid = (target_low + target_high) / 2

                # Weiterprojektion über das Ende des Fünfteilers hinaus: nach
                # Welle 5 folgt üblicherweise eine dreiteilige Korrektur A-B-C,
                # deren Welle A einen Teil der GESAMTEN Impulsbewegung (Start
                # bis projiziertes Welle-5-Ende) zurückgibt.
                sign     = 1.0 if up else -1.0
                full_len = abs(target_mid - v[0])
                a_lo, a_hi, a_mid = _leg_zone(target_mid, -sign, full_len, 0.382, 0.618)
                a_len = abs(a_mid - target_mid)
                b_lo, b_hi, b_mid = _leg_zone(a_mid, sign, a_len, 0.382, 0.618)
                c_lo, c_hi, _     = _leg_zone(b_mid, -sign, a_len, 0.618, 1.618)

                return {
                    "state": "wave5_forming",
                    "direction": "bullish" if up else "bearish",
                    "confidence": round(confidence, 3),
                    "target_low": target_low, "target_high": target_high,
                    "current_price": current_price, "current_date": current_date,
                    "last_pivot_date": window[-1]["date"], "last_pivot_price": p4,
                    "structure_type": "impulse",
                    "points": [
                        {"date": pt["date"], "price": pt["price"], "label": str(i)}
                        for i, pt in enumerate(window)
                    ],
                    "projected_waves": [
                        {"label": "A", "target_low": a_lo, "target_high": a_hi,
                         "note": "Korrektur nach Abschluss des Fünfteilers"},
                        {"label": "B", "target_low": b_lo, "target_high": b_hi,
                         "note": "Retracement von Welle A"},
                        {"label": "C", "target_low": c_lo, "target_high": c_hi,
                         "note": "Abschluss der Dreiteiler-Korrektur"},
                    ],
                    "expectation_text": (
                        f"Welle 4 scheint bei {p4:.2f} ({window[-1]['date']}) abgeschlossen — "
                        f"Welle 5 Richtung {target_low:.2f}–{target_high:.2f} erwartet. "
                        f"Danach (Korrektur des Fünfteilers): Welle A ({a_lo:.2f}–{a_hi:.2f}), "
                        f"Welle B ({b_lo:.2f}–{b_hi:.2f}), Welle C ({c_lo:.2f}–{c_hi:.2f})."
                    ),
                }

    # (A) Welle 3 in Bildung: Pivots 0,1,2 bestätigt (letzter = Wave-2-Ende)
    if len(pivots) >= 3:
        window = pivots[-3:]
        types = [p["type"] for p in window]
        expected = ["low", "high", "low"] if up else ["high", "low", "high"]
        if types == expected:
            v  = [p["price"] for p in window]
            vc = [flip(x) for x in v]
            len1 = vc[1] - vc[0]
            # Bestätigung: Kurs muss bereits über das Welle-1-Hoch hinausgelaufen
            # sein — sonst ist "Welle 2 abgeschlossen" nur eine unbestätigte
            # Vermutung (jeder gesunde Pullback in einem Trend würde sonst zählen).
            confirmed_break = flip(current_price) > vc[1]
            if confirmed_break and vc[1] > vc[0] and vc[2] > vc[0] and len1 > 0:
                w2_retrace = (vc[1] - vc[2]) / len1
                confidence = _fib_closeness(w2_retrace, FIB_RETRACE)
                p2 = v[2]
                t1 = p2 + flip(1.0 * len1)
                t2 = p2 + flip(2.618 * len1)
                target_low, target_high = min(t1, t2), max(t1, t2)
                target_mid = (target_low + target_high) / 2

                # Weiterprojektion innerhalb desselben Fünfteilers: Welle 4
                # korrigiert einen Teil von Welle 3, darf laut harter
                # Elliott-Regel aber nie das Kursgebiet von Welle 1 (v[1])
                # überschneiden — wird bei Bedarf hart daran geclippt. Welle 5
                # baut danach auf der ursprünglichen Welle-1-Länge auf.
                sign      = 1.0 if up else -1.0
                len3_proj = abs(target_mid - p2)
                w4_lo, w4_hi, _ = _leg_zone(target_mid, -sign, len3_proj, 0.236, 0.5)
                w1_bound = v[1]
                if up:
                    w4_lo = max(w4_lo, w1_bound)
                else:
                    w4_hi = min(w4_hi, w1_bound)
                w4_mid = (w4_lo + w4_hi) / 2
                w5_lo, w5_hi, _ = _leg_zone(w4_mid, sign, len1, 0.618, 1.618)

                # Limit-Einstiegspreis für die 2-3-Bewegung: 35%-Retracement
                # der 0-1-Länge, gemessen ab Welle 1 zurück Richtung Welle 0.
                entry_limit_price = flip(vc[1] - WAVE3_ENTRY_RETRACE_PCT * len1)
                valid_entry = (entry_limit_price > p2) if up else (entry_limit_price < p2)
                if not valid_entry:
                    # Welle 2 ist bereits tiefer als 35% zurückgelaufen -
                    # das Retracement-Level läge auf/unter dem strukturellen
                    # Stop (Welle-2-Tief) und wäre kein sinnvoller Einstieg.
                    entry_limit_price = None

                return {
                    "state": "wave3_forming",
                    "direction": "bullish" if up else "bearish",
                    "confidence": round(confidence, 3),
                    "target_low": target_low, "target_high": target_high,
                    "current_price": current_price, "current_date": current_date,
                    "last_pivot_date": window[-1]["date"], "last_pivot_price": p2,
                    "entry_limit_price": entry_limit_price,
                    "structure_type": "impulse",
                    "points": [
                        {"date": pt["date"], "price": pt["price"], "label": str(i)}
                        for i, pt in enumerate(window)
                    ],
                    "projected_waves": [
                        {"label": "4", "target_low": w4_lo, "target_high": w4_hi,
                         "note": "Korrektur von Welle 3, begrenzt durch Welle-1-Kursgebiet"},
                        {"label": "5", "target_low": w5_lo, "target_high": w5_hi,
                         "note": "Ausbruch über Welle-4-Ende, Ziel ≈ Welle-1-Länge"},
                    ],
                    "expectation_text": (
                        f"Welle 2 scheint bei {p2:.2f} ({window[-1]['date']}) abgeschlossen — "
                        f"Welle 3 Richtung {target_low:.2f}–{target_high:.2f} erwartet. "
                        + (
                            f"Limit-Einstieg bei Retracement auf {entry_limit_price:.2f} "
                            f"(35% von Welle 0-1). "
                            if entry_limit_price is not None else
                            "Kein sinnvolles Retracement-Level (Welle 2 bereits >35% "
                            "zurückgelaufen) — Einstieg zum aktuellen Kurs. "
                        )
                        + f"Danach: Welle 4 ({w4_lo:.2f}–{w4_hi:.2f}), "
                        f"Welle 5 ({w5_lo:.2f}–{w5_hi:.2f})."
                    ),
                }

    # (D) ABC-Korrektur gerade abgeschlossen -> evtl. neuer Impuls in "up"-Richtung
    if len(pivots) >= 4:
        window = pivots[-4:]
        hit = _check_corrective(window, bullish_prior=up)
        # Bestätigung: Kurs muss bereits spürbar (>=20% von Welle C) vom
        # C-Tiefpunkt zurückgelaufen sein, sonst ist der neue Impuls reine Spekulation.
        c_len = abs(window[-2]["price"] - window[-1]["price"])
        if hit and c_len > 0 and flip(current_price - window[-1]["price"]) >= 0.2 * c_len:
            return {
                "state": "new_impulse_possible",
                "direction": "bullish" if up else "bearish",
                "confidence": round(hit["confidence"] * 0.7, 3),  # spekulativer als etablierte Trendfortsetzung
                "target_low": None, "target_high": None,
                "current_price": current_price, "current_date": current_date,
                "last_pivot_date": window[-1]["date"], "last_pivot_price": window[-1]["price"],
                "structure_type": "impulse",
                "points": [
                    {"date": pt["date"], "price": pt["price"], "label": lbl}
                    for pt, lbl in zip(window, ["0", "A", "B", "C"])
                ],
                # Noch keine Weiterprojektion (Welle 1 selbst hat hier noch keine
                # bestätigte Länge, auf der Welle 2/3 aufbauen könnten) — das wäre
                # reine Spekulation auf Spekulation.
                "projected_waves": [],
                "expectation_text": (
                    f"ABC-Korrektur scheint bei {window[-1]['price']:.2f} "
                    f"({window[-1]['date']}) abgeschlossen — möglicher Start eines neuen "
                    f"Impulses (5-Teiler) in {'bullischer' if up else 'bearischer'} Richtung."
                ),
            }

    return None


def detect_elliott_waves(
    prices: pd.DataFrame,
    threshold_pct: float = 5.0,
    max_patterns: int = 4,
    min_confidence: float = 0.15,
) -> list[dict]:
    """Sucht in prices (Spalten: date, high, low) nach 5-Wellen-Impuls- und
    ABC-Korrekturstrukturen. Gibt die höchstens max_patterns überlappungsfreien
    Treffer mit der höchsten Konfidenz zurück, sortiert nach Startdatum.

    Rein strukturell/regelbasiert (keine ML-Vorhersage) — harte Elliott-Regeln
    (Welle 2 nicht über Start von Welle 1, Welle 3 nicht die kürzeste,
    Welle 4 ohne Überschneidung mit Welle 1) müssen erfüllt sein, sonst wird
    das Fenster verworfen statt erzwungen gefittet.
    """
    if prices.empty or len(prices) < 8:
        return []

    prices = prices.sort_values("date").reset_index(drop=True)
    dates = prices["date"].tolist()
    highs = prices["high"].tolist()
    lows  = prices["low"].tolist()

    pivots = _zigzag(dates, highs, lows, threshold_pct)
    if len(pivots) < 4:
        return []

    candidates = []
    for i in range(len(pivots) - 5):
        window = pivots[i:i + 6]
        types = [pt["type"] for pt in window]
        if types == ["low", "high", "low", "high", "low", "high"]:
            hit = _check_impulse(window, bullish=True)
        elif types == ["high", "low", "high", "low", "high", "low"]:
            hit = _check_impulse(window, bullish=False)
        else:
            hit = None
        if hit and hit["confidence"] >= min_confidence:
            candidates.append(hit)

    for i in range(len(pivots) - 3):
        window = pivots[i:i + 4]
        types = [pt["type"] for pt in window]
        if types == ["high", "low", "high", "low"]:
            hit = _check_corrective(window, bullish_prior=True)
        elif types == ["low", "high", "low", "high"]:
            hit = _check_corrective(window, bullish_prior=False)
        else:
            hit = None
        if hit and hit["confidence"] >= min_confidence:
            candidates.append(hit)

    # Greedy nicht-überlappende Auswahl nach Konfidenz, Impulse zuerst
    candidates.sort(key=lambda c: (c["type"] != "impulse", -c["confidence"]))
    selected, used_ranges = [], []
    for c in candidates:
        lo, hi = c["idx_range"]
        if any(not (hi < ulo or lo > uhi) for ulo, uhi in used_ranges):
            continue
        selected.append(c)
        used_ranges.append((lo, hi))
        if len(selected) >= max_patterns:
            break

    selected.sort(key=lambda c: c["points"][0]["date"])
    return selected
