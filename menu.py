"""
Interaktives Admin-Menü via Matrix-Polls.

Ablauf:
  !cmd              → Level-1 Poll (Kategorien)
  Nutzer wählt      → Level-2 Poll (Commands der Kategorie)
  Nutzer wählt      → Ausführung ODER Freitext-Prompt
  Nächste Nachricht → Ausführung mit Freitext-Input

Polls werden nach Auswahl redacted (gelöscht).
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Menü-Struktur
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MenuItem:
    label: str          # Anzeige im Poll
    cmd:   str          # Interner Command-Key
    prompt: str = ""    # Freitext-Prompt (leer = kein Input nötig)
    hint: str = ""      # Kurzer Hinweis nach dem Prompt


# Kategorien → Commands
MENU: Dict[str, List[MenuItem]] = {
    "👤 Spieler": [
        MenuItem("➕ Spieler hinzufügen",         "player_add",
                 "Matrix-ID oder Name eingeben:",
                 "z.B. @max:server  oder  Max  (Name wird aus Matrix-Profil gelesen)"),
        MenuItem("📊 Feldspieler-Score setzen",   "player_set_field",
                 "Name und Score eingeben:",
                 "z.B.  Max 7.5"),
        MenuItem("🧤 Torwart-Score setzen",        "player_set_gk",
                 "Name und Score eingeben:",
                 "z.B.  Max 8.0"),
        MenuItem("🔄 GK-Fähigkeit umschalten",    "player_toggle_gk",
                 "Spieler eingeben:",
                 "Name oder @user:server"),
        MenuItem("❌ Spieler deaktivieren",        "player_del",
                 "Spieler eingeben:",
                 "Name oder @user:server"),
    ],
    "⚽ Spieltag": [
        MenuItem("🎲 Team-Vorschlag generieren",   "team_next"),
        MenuItem("🔀 Weiteren Vorschlag",          "team_alt"),
        MenuItem("✅ Vorschlag aktivieren",        "team_select",
                 "Vorschlag-Buchstabe eingeben:",
                 "z.B.  A  oder  B"),
        MenuItem("🗳️ Vorschläge zur Abstimmung",  "team_vote"),
        MenuItem("👤 Gastspieler hinzufügen",      "match_guest",
                 "Name und optionalen Score eingeben:",
                 'z.B.  "Max Mustermann"  oder  "Max" 7.5'),
        MenuItem("🔄 Spieler tauschen/verschieben","match_change",
                 "Ein oder zwei Namen eingeben:",
                 "z.B.  Max  oder  Max Anna"),
        MenuItem("🧤 Torwart setzen",              "match_setgk",
                 "Spieler eingeben:",
                 "Name des neuen Torwarts"),
        MenuItem("🔕 Spieler nicht werten",        "match_switched",
                 "Spieler eingeben:",
                 "Name des Spielers"),
        MenuItem("📝 Ergebnis eintragen",          "result",
                 "Ergebnis eingeben:",
                 "z.B.  3:2"),
        MenuItem("🗓️ Vote starten",               "vote"),
    ],
    "📊 Auswertung": [
        MenuItem("👥 Spielerliste anzeigen",       "player_list"),
        MenuItem("📋 Letzte Ergebnisse",           "match_history"),
        MenuItem("📈 Scores anzeigen",             "scores"),
    ],
}

CATEGORIES = list(MENU.keys())


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MenuState:
    user:             str            # Matrix-ID des Nutzers im Menü-Flow
    level:            int   = 0      # 1 = Kategorie-Poll offen, 2 = Command-Poll offen, 3 = Freitext
    category:         str   = ""
    command:          str   = ""
    poll_event_ids:   List[str] = field(default_factory=list)   # alle offenen Poll-IDs
    prompt_msg_id:    Optional[str] = None  # Prompt-Nachricht (zum Löschen)


class MenuManager:
    def __init__(self):
        # room_id → MenuState
        self._states: Dict[str, MenuState] = {}

    def get(self, room_id: str) -> Optional[MenuState]:
        return self._states.get(room_id)

    def start(self, room_id: str, user: str) -> MenuState:
        state = MenuState(user=user, level=1)
        self._states[room_id] = state
        return state

    def clear(self, room_id: str):
        self._states.pop(room_id, None)

    def is_active(self, room_id: str) -> bool:
        return room_id in self._states

    def awaiting_text(self, room_id: str, user: str) -> bool:
        s = self._states.get(room_id)
        return s is not None and s.level == 3 and s.user == user


# ─────────────────────────────────────────────────────────────────────────────
# Poll-Content Helpers
# ─────────────────────────────────────────────────────────────────────────────

def category_poll_content() -> dict:
    answers_msc = [
        {"id": f"cat_{i}", "org.matrix.msc3381.poll.answer.text": cat}
        for i, cat in enumerate(CATEGORIES)
    ]
    answers_stable = [
        {"id": f"cat_{i}", "m.text": cat}
        for i, cat in enumerate(CATEGORIES)
    ]
    return {
        "msgtype": "m.text",
        "body": "🤖 TeamBot – Was möchtest du tun?",
        "org.matrix.msc3381.poll.start": {
            "kind": "org.matrix.msc3381.poll.disclosed",
            "max_selections": 1,
            "question": {"body": "🤖 TeamBot – Was möchtest du tun?"},
            "answers": answers_msc,
        },
        "m.poll.start": {
            "kind": "m.poll.disclosed",
            "max_selections": 1,
            "question": {"body": "🤖 TeamBot – Was möchtest du tun?"},
            "answers": answers_stable,
        },
    }


def command_poll_content(category: str) -> dict:
    items = MENU[category]
    answers_msc = [
        {"id": f"cmd_{i}", "org.matrix.msc3381.poll.answer.text": item.label}
        for i, item in enumerate(items)
    ]
    answers_stable = [
        {"id": f"cmd_{i}", "m.text": item.label}
        for i, item in enumerate(items)
    ]
    title = f"{category} – Was genau?"
    return {
        "msgtype": "m.text",
        "body": title,
        "org.matrix.msc3381.poll.start": {
            "kind": "org.matrix.msc3381.poll.disclosed",
            "max_selections": 1,
            "question": {"body": title},
            "answers": answers_msc,
        },
        "m.poll.start": {
            "kind": "m.poll.disclosed",
            "max_selections": 1,
            "question": {"body": title},
            "answers": answers_stable,
        },
    }


def parse_category_answer(answer_id: str) -> Optional[str]:
    """'cat_2' → CATEGORIES[2]"""
    if answer_id.startswith("cat_"):
        try:
            return CATEGORIES[int(answer_id[4:])]
        except (ValueError, IndexError):
            pass
    return None


def parse_command_answer(category: str, answer_id: str) -> Optional[MenuItem]:
    """'cmd_3' → MENU[category][3]"""
    if answer_id.startswith("cmd_"):
        try:
            return MENU[category][int(answer_id[4:])]
        except (ValueError, IndexError):
            pass
    return None
