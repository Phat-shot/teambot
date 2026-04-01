"""
Team-Building via Snake-Draft.

Ablauf:
  1. GK-Zuweisung: NUR explizite 🥅-Freiwillige (je einer pro Team).
     Falls kein/ein Freiwilliger → kein GK gesetzt (Team entscheidet vor Ort).
  2. Snake-Draft: Feldspieler sortiert nach effective_score (absteigend).
     Pick 1→Gelb, 2→Bunt, 3→Gelb, 4→Bunt … bei ungerader Zahl letzter→Bunt.
  3. Gast-Matching: Gäste werden nach Möglichkeit ins Team ihres Mitbringers gesetzt.
  4. Injury-Penalty: Spieler mit 🩹 werden für das Matching um 2 Punkte runtergerankt
     (nur temporär, score_base bleibt unverändert).
"""

import random
from typing import Dict, List, Optional, Tuple


TEAM1_NAME = "Team Gelb 🟡"
TEAM2_NAME = "Team Bunt 🌈"
INJURY_PENALTY = 2.0


def effective_score(player: Dict) -> float:
    base = float(player.get("score", player.get("score_field", 5.0)))
    if player.get("injured"):
        base = max(0.0, base - INJURY_PENALTY)
    return round(base, 2)


def build_teams(
    players: List[Dict],
    gk_volunteers: List[str],
) -> Tuple[List[Dict], Optional[Dict], List[Dict], Optional[Dict]]:
    """
    Baut zwei Teams via Snake-Draft.
    gk_volunteers: Liste von matrix_ids die sich als GK gemeldet haben.
    """
    by_id = {p["matrix_id"]: p for p in players if "matrix_id" in p}

    # ── GK-Zuweisung: nur Freiwillige ────────────────────────────────────
    vols = [by_id[m] for m in gk_volunteers if m in by_id]
    vols.sort(key=effective_score, reverse=True)

    if len(vols) >= 2:
        gk1, gk2 = vols[0], vols[1]
    elif len(vols) == 1:
        gk1, gk2 = vols[0], None
    else:
        gk1 = gk2 = None

    assigned = {p["id"] for p in [gk1, gk2] if p and "id" in p}
    field = [p for p in players if p.get("id") not in assigned and not (
        p.get("is_guest") and p.get("id") in assigned
    )]
    # Gäste haben keine id in assigned – handle via matrix_id check
    gk_matrix_ids = {p["matrix_id"] for p in [gk1, gk2] if p and "matrix_id" in p}
    field = [p for p in players if p.get("matrix_id") not in gk_matrix_ids or p.get("is_guest")]
    # Guests assigned as GK should still be excluded
    field = [p for p in field if not (p.get("is_guest") and p.get("matrix_id") in gk_matrix_ids)]

    # ── Snake-Draft ───────────────────────────────────────────────────────
    sorted_field = sorted(field, key=effective_score, reverse=True)
    t1_field: List[Dict] = []
    t2_field: List[Dict] = []

    for i, player in enumerate(sorted_field):
        # Runde bestimmen (0-basiert): Runde = i // 2
        # Snake: gerade Runden → 1,2; ungerade → 2,1
        runde = i // 2
        pos_in_round = i % 2
        if runde % 2 == 0:
            (t1_field if pos_in_round == 0 else t2_field).append(player)
        else:
            (t2_field if pos_in_round == 0 else t1_field).append(player)

    # Bei ungerader Anzahl bekommt Bunt den letzten (bereits durch Snake korrekt)

    # ── Gast-Matching ─────────────────────────────────────────────────────
    t1_field, t2_field = _match_guests_to_hosts(t1_field, t2_field, gk1, gk2)

    return t1_field, gk1, t2_field, gk2


def _match_guests_to_hosts(
    t1_field: List[Dict],
    t2_field: List[Dict],
    gk1: Optional[Dict],
    gk2: Optional[Dict],
) -> Tuple[List[Dict], List[Dict]]:
    t1_ids = {p.get("matrix_id") for p in t1_field} | ({gk1.get("matrix_id")} if gk1 else set())
    t2_ids = {p.get("matrix_id") for p in t2_field} | ({gk2.get("matrix_id")} if gk2 else set())

    for _ in range(10):
        swapped = False
        for guest in list(t1_field):
            if not guest.get("is_guest"):
                continue
            host = guest.get("matrix_id")
            if host and host in t2_ids:
                cands = [p for p in t2_field if not p.get("is_guest")]
                if not cands:
                    continue
                swap = min(cands, key=lambda p: abs(effective_score(p) - effective_score(guest)))
                t1_field.remove(guest); t2_field.remove(swap)
                t1_field.append(swap); t2_field.append(guest)
                t1_ids = {p.get("matrix_id") for p in t1_field} | ({gk1.get("matrix_id")} if gk1 else set())
                t2_ids = {p.get("matrix_id") for p in t2_field} | ({gk2.get("matrix_id")} if gk2 else set())
                swapped = True; break
        for guest in list(t2_field):
            if not guest.get("is_guest"):
                continue
            host = guest.get("matrix_id")
            if host and host in t1_ids:
                cands = [p for p in t1_field if not p.get("is_guest")]
                if not cands:
                    continue
                swap = min(cands, key=lambda p: abs(effective_score(p) - effective_score(guest)))
                t2_field.remove(guest); t1_field.remove(swap)
                t2_field.append(swap); t1_field.append(guest)
                t1_ids = {p.get("matrix_id") for p in t1_field} | ({gk1.get("matrix_id")} if gk1 else set())
                t2_ids = {p.get("matrix_id") for p in t2_field} | ({gk2.get("matrix_id")} if gk2 else set())
                swapped = True; break
        if not swapped:
            break
    return t1_field, t2_field


def format_teams(
    t1_field: List[Dict],
    gk1: Optional[Dict],
    t2_field: List[Dict],
    gk2: Optional[Dict],
) -> str:
    """Admin-Format mit Scores."""
    def gk_line(gk: Optional[Dict]) -> str:
        if not gk:
            return "  🧤 — (wird vor Ort entschieden)"
        score = effective_score(gk)
        inj = " 🩹" if gk.get("injured") else ""
        tag = " 👤" if gk.get("is_guest") else (" ⭐" if gk.get("can_gk") else "")
        return f"  🧤 {gk['display_name']} ({score:.2f}){tag}{inj}"

    def field_line(p: Dict) -> str:
        score = effective_score(p)
        inj = " 🩹" if p.get("injured") else ""
        guest = " 👤" if p.get("is_guest") else ""
        return f"  ⚽ {p['display_name']} ({score:.2f}){guest}{inj}"

    t1_total = sum(effective_score(p) for p in t1_field) + (effective_score(gk1) if gk1 else 0)
    t2_total = sum(effective_score(p) for p in t2_field) + (effective_score(gk2) if gk2 else 0)
    diff = abs(t1_total - t2_total)

    lines = [
        "⚽ **Mannschaften** ⚽", "",
        f"**{TEAM1_NAME}**  |  Stärke: {t1_total:.2f}",
        gk_line(gk1),
        *[field_line(p) for p in t1_field],
        "",
        f"**{TEAM2_NAME}**  |  Stärke: {t2_total:.2f}",
        gk_line(gk2),
        *[field_line(p) for p in t2_field],
        "",
        f"⚖️ Differenz: {diff:.2f}",
    ]
    return "\n".join(lines)


def format_teams_main(
    t1_field: List[Dict],
    gk1: Optional[Dict],
    t2_field: List[Dict],
    gk2: Optional[Dict],
) -> str:
    """Hauptraum-Format ohne Scores."""
    def gk_line(gk: Optional[Dict]) -> str:
        if not gk:
            return None  # type: ignore
        tag = " 👤" if gk.get("is_guest") else ""
        inj = " 🩹" if gk.get("injured") else ""
        return f"  🧤 {gk['display_name']}{tag}{inj}"

    def field_line(p: Dict) -> str:
        guest = " 👤" if p.get("is_guest") else ""
        inj = " 🩹" if p.get("injured") else ""
        return f"  ⚽ {p['display_name']}{guest}{inj}"

    lines = ["⚽ **Mannschaften**", "", f"**{TEAM1_NAME}**"]
    gl = gk_line(gk1)
    if gl:
        lines.append(gl)
    lines += [field_line(p) for p in t1_field]
    lines += ["", f"**{TEAM2_NAME}**"]
    gl2 = gk_line(gk2)
    if gl2:
        lines.append(gl2)
    lines += [field_line(p) for p in t2_field]
    return "\n".join(lines)
