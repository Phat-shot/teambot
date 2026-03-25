"""
Team balancing mit GK-Logik.

GK-Zuweisung (Priorität):
  1. 🥅-Freiwillige (nach score sortiert, einer pro Team)
  2. can_gk=True Spieler mit bestem score (einer pro Team)
  3. Spieler mit niedrigstem score pro Team (Fallback)

Effektiver Score: einheitlicher score-Wert für alle Spieler (kein GK-Score mehr).
"""

import random
from itertools import combinations
from typing import Dict, List, Optional, Tuple


def effective_score(player: Dict) -> float:
    return round(float(player.get("score", player.get("score_field", 5.0))), 2)


TEAM1_NAME = "Team Gelb 🟡"
TEAM2_NAME = "Team Bunt 🌈"


def assign_gks(
    players: List[Dict],
    gk_volunteers: List[str],
) -> Tuple[Optional[Dict], Optional[Dict], List[Dict]]:
    """Returns (gk1, gk2, remaining_field_players)."""
    n = len(players)
    if n < 2:
        return None, None, players

    by_id = {p["matrix_id"]: p for p in players if "matrix_id" in p}

    # ① Freiwillige
    vols = [by_id[m] for m in gk_volunteers if m in by_id]
    vols.sort(key=effective_score, reverse=True)

    # ② can_gk Spieler (nicht bereits Freiwillige)
    vol_ids = {p["id"] for p in vols}
    can_gk_players = sorted(
        [p for p in players if p.get("can_gk") and p["id"] not in vol_ids],
        key=effective_score, reverse=True,
    )

    gk_pool = vols + can_gk_players

    if len(gk_pool) >= 2:
        gk1, gk2 = gk_pool[0], gk_pool[1]
    elif len(gk_pool) == 1:
        gk1, gk2 = gk_pool[0], None
    else:
        gk1 = gk2 = None

    assigned = {p["id"] for p in [gk1, gk2] if p}
    field = [p for p in players if p["id"] not in assigned]

    # ③ Fallback
    if gk1 is None and field:
        fallback = min(field, key=effective_score)
        gk1 = fallback
        field = [p for p in field if p["id"] != fallback["id"]]
    if gk2 is None and field:
        fallback = min(field, key=effective_score)
        gk2 = fallback
        field = [p for p in field if p["id"] != fallback["id"]]

    return gk1, gk2, field


def balance_field_players(players: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    n = len(players)
    if n == 0:
        return [], []
    if n == 1:
        return players, []

    half = n // 2
    best_diff = float("inf")
    best_t2: frozenset = frozenset()

    for combo in combinations(range(n), half):
        t2_score = sum(effective_score(players[i]) for i in combo)
        t1_score = sum(effective_score(players[i]) for i in range(n) if i not in combo)
        diff = abs(t1_score - t2_score)
        if diff < best_diff:
            best_diff = diff
            best_t2 = frozenset(combo)

    team1 = [players[i] for i in range(n) if i not in best_t2]
    team2 = [players[i] for i in best_t2]
    random.shuffle(team1)
    random.shuffle(team2)
    return team1, team2


def build_teams(
    players: List[Dict],
    gk_volunteers: List[str],
) -> Tuple[List[Dict], Optional[Dict], List[Dict], Optional[Dict]]:
    gk1, gk2, field = assign_gks(players, gk_volunteers)
    t1_field, t2_field = balance_field_players(field)
    return t1_field, gk1, t2_field, gk2


def format_teams(
    t1_field: List[Dict],
    gk1: Optional[Dict],
    t2_field: List[Dict],
    gk2: Optional[Dict],
) -> str:
    def gk_line(gk: Optional[Dict]) -> str:
        if not gk:
            return "  🧤 — (kein Torwart)"
        score = effective_score(gk)
        tag = " 👤" if gk.get("is_guest") else (" ⭐" if gk.get("can_gk") else " (Fallback)")
        return f"  🧤 {gk['display_name']} ({score:.2f}){tag}"

    def field_line(p: Dict) -> str:
        score = effective_score(p)
        gk_tag = " 🥅" if p.get("can_gk") else ""
        guest = " 👤" if p.get("is_guest") else ""
        return f"  ⚽ {p['display_name']} ({score:.2f}){gk_tag}{guest}"

    t1_total = sum(effective_score(p) for p in t1_field) + (effective_score(gk1) if gk1 else 0)
    t2_total = sum(effective_score(p) for p in t2_field) + (effective_score(gk2) if gk2 else 0)
    diff = abs(t1_total - t2_total)

    lines = [
        "⚽ **Mannschaften** ⚽",
        "",
        f"🟡 **{TEAM1_NAME}**  |  Stärke: {t1_total:.2f}",
        gk_line(gk1),
        *[field_line(p) for p in t1_field],
        "",
        f"🌈 **{TEAM2_NAME}**  |  Stärke: {t2_total:.2f}",
        gk_line(gk2),
        *[field_line(p) for p in t2_field],
        "",
        f"⚖️ Differenz: {diff:.2f}",
    ]
    all_guests = [p for p in (t1_field + t2_field + [gk1, gk2]) if p and p.get("is_guest")]
    if all_guests:
        lines.append(f"👤 Gäste (kein Score-Update): {', '.join(g['display_name'] for g in all_guests)}")
    return "\n".join(lines)
