"""
Interaktives Admin-Menü via Matrix-Polls.

Struktur:
  Level 1: Hauptkategorien – 1. Spieler  2. Matchday  3. Team
  Level 2: Unterpunkte je Kategorie (teils als Poll mit Raumnutzern)
  Level 3: Freitext oder Score-Poll

Alle Polls werden nach Auswahl gelöscht.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from poll import make_poll


# ─────────────────────────────────────────────────────────────────────────────
# Kategorien (Level 1)
# ─────────────────────────────────────────────────────────────────────────────

CATEGORIES = [
    ("cat_player",   "👤 Spieler"),
    ("cat_matchday", "📅 Matchday"),
    ("cat_team",     "⚽ Team"),
]


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MenuState:
    user:           str
    level:          int  = 1
    category:       str  = ""
    command:        str  = ""
    poll_event_ids: List[str] = field(default_factory=list)
    prompt_msg_id:  Optional[str] = None
    # Für Spieler-Select-Flow (Ändern/Löschen)
    selected_matrix_id: Optional[str] = None
    # Für Score-Poll
    score_poll_answers: Dict[str, float] = field(default_factory=dict)


class MenuManager:
    def __init__(self):
        self._states: Dict[str, MenuState] = {}

    def get(self, room_id: str) -> Optional[MenuState]:
        return self._states.get(room_id)

    def start(self, room_id: str, user: str) -> MenuState:
        state = MenuState(user=user, level=1)
        self._states[room_id] = state
        return state

    def clear(self, room_id: str):
        self._states.pop(room_id, None)

    def awaiting_text(self, room_id: str, user: str) -> bool:
        s = self._states.get(room_id)
        return s is not None and s.level == 3 and s.user == user


# ─────────────────────────────────────────────────────────────────────────────
# Poll-Content Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _with_back(answers: list) -> list:
    """Fügt ↩️ Zurück als letzte Option hinzu."""
    return answers + [("back", "↩️ Zurück")]


def main_menu_poll() -> dict:
    return make_poll(
        "🤖 TeamBot – Was möchtest du tun?",
        CATEGORIES,
    )


def player_menu_poll() -> dict:
    return make_poll(
        "👤 Spieler – Was möchtest du tun?",
        _with_back([
            ("pl_add",  "➕ Hinzufügen"),
            ("pl_edit", "✏️ Score ändern"),
            ("pl_del",  "❌ Löschen"),
        ]),
    )


def player_select_poll(players: list, action_label: str) -> dict:
    answers = [(f"ps_{p['id']}", p["display_name"]) for p in players]
    return make_poll(f"👤 Spieler auswählen – {action_label}", _with_back(answers))


def room_members_poll(members: list, page: int = 0) -> dict:
    """Poll mit Raum-Mitgliedern, max 16 pro Seite + Navigation. Matrix-Limit: 20 Antworten."""
    PAGE_SIZE = 16
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_members = members[start:end]
    answers = [(f"rm_{start + i}", name) for i, (_, name, _) in enumerate(page_members)]
    nav = []
    if page > 0:
        nav.append((f"page_{page - 1}", "⬅️ Vorherige Seite"))
    if end < len(members):
        nav.append((f"page_{page + 1}", "➡️ Nächste Seite"))
    nav.append(("back", "↩️ Abbrechen"))
    total = len(members)
    title = (
        f"➕ Wen hinzufügen? (Seite {page + 1}/{(total - 1) // PAGE_SIZE + 1})"
        if total > PAGE_SIZE else "➕ Wen hinzufügen?"
    )
    return make_poll(title, answers + nav)


def score_poll() -> dict:
    scores = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5,
              5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0]
    answers = [(f"sc_{int(s*10)}", str(s)) for s in scores]
    return make_poll("📊 Score vergeben (0–10):", _with_back(answers))


def matchday_menu_poll(vote_open: bool) -> dict:
    if vote_open:
        return make_poll(
            "📅 Matchday – Vote ist offen:",
            _with_back([
                ("md_team",   "⚽ Team erstellen"),
                ("md_result", "📝 Ergebnis eintragen"),
            ]),
        )
    else:
        return make_poll(
            "📅 Matchday – Kein Vote offen:",
            _with_back([
                ("md_vote",   "🗓️ Vote starten"),
                ("md_result", "📝 Ergebnis eintragen"),
            ]),
        )


def team_menu_poll() -> dict:
    return make_poll(
        "⚽ Team – Was möchtest du tun?",
        _with_back([
            ("tm_next",    "🎲 Team-Vorschlag generieren"),
            ("tm_alt",     "🔀 Weiteren Vorschlag"),
            ("tm_select",  "✅ Vorschlag aktivieren"),
            ("tm_guest",   "👤 Gast hinzufügen"),
            ("tm_change",  "🔄 Spieler tauschen"),
            ("tm_gk",      "🧤 Torwart setzen"),
            ("tm_switch",  "🔕 Spieler nicht werten"),
            ("tm_announce","📣 Team ankündigen"),
        ]),
    )
