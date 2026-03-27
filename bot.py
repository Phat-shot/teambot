"""
TeamBot – Matrix-Bot für die wöchentliche Fußball-Teamaufstellung.

════════════════════════════════════════════════════════
BEFEHLE (öffentlich)
  !player              Spielerliste mit Scores
  !match [N]           Letzte 5 (oder N) Ergebnisse
  !gk                  Als Torwart für dieses Spiel melden
  !kein_gk             GK-Meldung zurückziehen
  !team                Teams aus aktuellem Vote generieren
  !help                Diese Hilfe

BEFEHLE (Admin – schreibend)
  !vote                         Vote sofort starten
  !result #:#                   Ergebnis eintragen + Scores neu berechnen

  !player add @id Name [gk]     Spieler anlegen  (gk = Torwart-fähig)
  !player set @id [field|gk] N  Score setzen  (default: field)
  !player gk @id                GK-Fähigkeit ein/aus  (Score bleibt)
  !player del @id               Spieler deaktivieren

  !match change Name1 [Name2]   Spieler tauschen / ins andere Team verschieben
  !match gk Name                Spieler als Torwart seines Teams setzen
  !match switched Name          Spieler von Score-Wertung aus-/einschließen
════════════════════════════════════════════════════════
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from nio import (
    AsyncClient,
    InviteMemberEvent,
    LoginResponse,
    ReactionEvent,
    RoomMessageText,
    RoomSendResponse,
    SyncResponse,
    UnknownEvent,
)

from config import Config
from db import Database
from menu import (MenuManager, MenuState,
                  main_menu_poll, player_menu_poll, player_select_poll,
                  room_members_poll, score_poll,
                  matchday_menu_poll, team_menu_poll)
from poll import make_poll, POLL_EVENT_TYPE, POLL_RESPONSE_TYPES, POLL_RESPONSE_KEYS
from teams import build_teams, format_teams, format_teams_main, effective_score, TEAM1_NAME, TEAM2_NAME

TEAM1_LABEL = TEAM1_NAME
TEAM2_LABEL = TEAM2_NAME

SYNC_TOKEN_PATH = "data/sync_token"
MAX_EVENT_AGE_SECONDS = 7 * 24 * 3600   # 1 Woche

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
HELP_TEXT = """\
**⚽ TeamBot – Admin-Befehle** _(nur im Admin-Raum)_

**Team-Verwaltung**
`!team`          – Neuen Team-Vorschlag generieren (A, B, C, …)
`!team A`        – Vorschlag A aktivieren
`!team vote`     – Alle Vorschläge zur Abstimmung stellen
`!vote`          – Vote sofort starten

**Spieler-Stammdaten**
`!player`                            – Spielerliste
`!player add @user:server [Name] [gk]` – Spieler anlegen
`!player set Name 7.5`               – Feldspieler-Score setzen
`!player set Name gk 8.0`            – Torwart-Score setzen
`!player gk Name`                    – GK-Fähigkeit ein/aus
`!player del Name`                   – Spieler deaktivieren

**Spieltag-Korrekturen**
`!match change Name1 [Name2]`        – Spieler tauschen/verschieben
`!match gk Name`                     – Torwart setzen
`!match switched Name`               – Score-Wertung ein-/ausschalten
`!match guest "Name" [Score]`        – Gastspieler hinzufügen
`!result 3:2`                        – Ergebnis + Score-Update

**Interaktives Menü**
`!cmd`           – Geführtes Poll-Menü starten

**Team-Poll (Admin-Gruppe)**
Nach `!team` erscheint ein Poll mit allen Spielern.
Spieler auswählen → mit Emoji reagieren:
🔃 = Team wechseln  |  🥅 = Als Torwart setzen
1️⃣–9️⃣ = N Gäste hinzufügen (fair verteilt)
📣 = Team in Hauptgruppe ankündigen

**Score-System**
🟡 Team Gelb  vs  🌈 Team Bunt
Feldspieler: `field` · GK-fähig: `0,5 × field + 0,5 × gk`
Neuberechnung: 50 % letzter Score · 30 % letzte 5 Spiele · 20 % letztes Spiel

**Torwart-Zuweisung**
① 🥅-Reaktion auf den Vote-Poll (Freiwillige)
② GK-fähige Spieler nach GK-Score
③ Fallback: schwächster Spieler pro Team
"""


class TeamBot:
    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.db_path)
        self.client = AsyncClient(config.homeserver, config.user_id)
        self.poll_client: Optional[AsyncClient] = None
        self.scheduler = AsyncIOScheduler(timezone="Europe/Berlin")

        # Aktuell aktive Teams (für Korrekturen und !result)
        self._t1_field:   List[Dict]    = []
        self._t1_gk:      Optional[Dict] = None
        self._t2_field:   List[Dict]    = []
        self._t2_gk:      Optional[Dict] = None
        self._switched:   set           = set()

        # Gäste – bleiben bis zum nächsten Vote erhalten
        self._guests:     List[Dict]    = []

        # Team-Vorschläge A, B, C, …
        self._proposals:         Dict[str, tuple] = {}   # letter → (t1f, gk1, t2f, gk2)
        self._proposal_players:  frozenset        = frozenset()  # player-IDs des letzten Vorschlagsdurchlaufs
        self._active_proposal:   Optional[str]    = None
        self._proposal_poll_id:  Optional[str]    = None   # Matrix event_id des Vorschlags-Polls
        self._proposal_votes:    Dict[str, int]   = {}     # letter → Stimmenzahl

        # Admin-Team-Poll (in Admin-Gruppe: Spielerauswahl + Aktions-Emoji)
        self._admin_team_poll_id:  Optional[str]  = None   # event_id des Admin-Team-Polls
        self._admin_team_poll_map: Dict[str, Dict] = {}    # answer_id → player dict
        self._admin_team_selection: Dict[str, List[str]] = {}  # sender → [answer_ids]

        # Letzte Team-Ankündigung im Hauptraum (für Redact beim Tausch)
        self._last_team_msg_id: Optional[str] = None

        # Interaktives Admin-Menü
        self._menu = MenuManager()

    # ─────────────────────────────────────────────────────────────────────────
    # Start
    # ─────────────────────────────────────────────────────────────────────────

    async def start(self):
        await self.db.connect()

        resp = await self.client.login(self.config.password)
        if not isinstance(resp, LoginResponse):
            raise RuntimeError(f"Matrix-Login fehlgeschlagen: {resp}")
        logger.info("Eingeloggt als %s", self.config.user_id)

        # Zweiter Client für Poll-Versand – kein sync_forever, nur senden
        if self.config.poll_sender_id and self.config.poll_sender_password:
            self.poll_client = AsyncClient(self.config.homeserver, self.config.poll_sender_id)
            pr = await self.poll_client.login(self.config.poll_sender_password)
            if isinstance(pr, LoginResponse):
                logger.info("Poll-Sender eingeloggt als %s", self.config.poll_sender_id)
            else:
                logger.warning("Poll-Sender Login fehlgeschlagen (%s) – Polls werden als Bot gesendet", pr)
                await self.poll_client.close()
                self.poll_client = None


        self.client.add_event_callback(self._on_message, RoomMessageText)
        self.client.add_event_callback(self._on_reaction, UnknownEvent)
        self.client.add_event_callback(self._on_reaction, ReactionEvent)
        self.client.add_event_callback(self._on_invite, InviteMemberEvent)
        self.client.add_response_callback(self._on_sync, SyncResponse)

        self._setup_scheduler()
        self.scheduler.start()

        # Sync-Token laden – Bot setzt nach Neustart genau hier fort
        since = _load_sync_token()
        if since:
            logger.info("Sync-Token geladen – überspringe bereits verarbeitete Events")
        else:
            logger.info("Kein Sync-Token – erster Start, verarbeite nur neue Events")

        logger.info("Bot läuft – Sync-Loop startet …")
        await self.client.sync_forever(
            timeout=30_000,
            full_state=True,
            since=since,
        )

    def _setup_scheduler(self):
        cfg = self.config
        self.scheduler.add_job(
            self._scheduled_vote,
            CronTrigger(day_of_week=cfg.vote_weekday,
                        hour=cfg.vote_hour, minute=cfg.vote_minute),
            id="weekly_vote",
        )
        self.scheduler.add_job(
            self._scheduled_teams,
            CronTrigger(day_of_week=cfg.team_weekday,
                        hour=cfg.team_hour, minute=cfg.team_minute),
            id="weekly_teams",
        )
        # Sonntag 10:00 – meistgewählten Vorschlag aktivieren
        self.scheduler.add_job(
            self._scheduled_apply_voted_proposal,
            CronTrigger(day_of_week=cfg.team_weekday, hour=10, minute=0),
            id="apply_proposal",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Hilfsmethoden
    # ─────────────────────────────────────────────────────────────────────────

    def _is_admin(self, matrix_id: str) -> bool:
        """Admin = Mitglied im konfigurierten Admin-Raum."""
        admin_room = self.client.rooms.get(self.config.admin_room_id)
        if not admin_room:
            return False
        return matrix_id in admin_room.users

    async def _resolve_player(self, query: str) -> Optional[Dict]:
        """
        Spieler per Matrix-ID (@user:server) oder Anzeigenamen finden.
        Gibt None zurück wenn nicht gefunden.
        """
        return await self.db.find_player(query)

    def _has_teams(self) -> bool:
        return bool(self._t1_field or self._t1_gk or self._t2_field or self._t2_gk)

    def _reset_match_state(self):
        """Reset Teams nach !result – Gäste bleiben bis zum nächsten Vote."""
        self._t1_field = []
        self._t1_gk    = None
        self._t2_field = []
        self._t2_gk    = None
        self._switched = set()
        # _guests bleibt erhalten!

    def _reset_proposals(self):
        """Vorschläge zurücksetzen (neue Spielerliste oder neuer Vote)."""
        self._proposals        = {}
        self._proposal_players = frozenset()
        self._active_proposal  = None
        self._proposal_poll_id = None
        self._proposal_votes   = {}
        # _guests wird NICHT hier zurückgesetzt – nur bei neuem Vote-Start

    def _current_teams_text(self) -> str:
        return format_teams(self._t1_field, self._t1_gk, self._t2_field, self._t2_gk)

    def _current_teams_text_main(self) -> str:
        return format_teams_main(self._t1_field, self._t1_gk, self._t2_field, self._t2_gk)

    async def send(self, text: str, room_id: Optional[str] = None) -> Optional[str]:
        """Nachricht senden. Ohne room_id → Hauptraum."""
        target = room_id or self.config.room_id
        content = {
            "msgtype": "m.text",
            "body": text,
            "format": "org.matrix.custom.html",
            "formatted_body": _md_to_html(text),
        }
        resp = await self.client.room_send(target, "m.room.message", content)
        if isinstance(resp, RoomSendResponse):
            return resp.event_id
        logger.error("send() fehlgeschlagen in %s: %s", target, resp)
        return None

    async def send_main(self, text: str) -> Optional[str]:
        """Nachricht in Hauptraum – über poll_client (gebridgter User) falls konfiguriert."""
        client = self.poll_client if self.poll_client else self.client
        content = {
            "msgtype": "m.text",
            "body": text,
            "format": "org.matrix.custom.html",
            "formatted_body": _md_to_html(text),
        }
        resp = await client.room_send(self.config.room_id, "m.room.message", content)
        if isinstance(resp, RoomSendResponse):
            return resp.event_id
        logger.error("send_main() fehlgeschlagen: %s", resp)
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Event-Callbacks
    # ─────────────────────────────────────────────────────────────────────────

    async def _on_invite(self, room, event):
        if event.membership != "invite":
            return
        if event.state_key != self.config.user_id:
            return
        logger.info("Einladung zu %s – trete bei …", room.room_id)
        resp = await self.client.join(room.room_id)
        if hasattr(resp, "room_id"):
            logger.info("Beigetreten: %s", resp.room_id)
            if resp.room_id == self.config.room_id:
                await self.send("👋 TeamBot ist aktiv! `!help` für alle Befehle.")
        else:
            logger.error("Beitritt fehlgeschlagen %s: %s", room.room_id, resp)

    async def _on_sync(self, response):
        """Sync-Token nach jedem erfolgreichen Sync persistieren."""
        _save_sync_token(response.next_batch)

    async def _on_message(self, room, event):
        if event.sender == self.config.user_id:
            return

        # Events älter als 1 Woche ignorieren (Schutz nach Neustart)
        age_ms = getattr(event, "age", None)
        if age_ms is None:
            ts = getattr(event, "server_timestamp", 0)
            age_ms = (datetime.now().timestamp() * 1000) - ts
        if age_ms > MAX_EVENT_AGE_SECONDS * 1000:
            logger.debug("Event übersprungen (zu alt: %.0fs): %s", age_ms/1000, event.event_id)
            return

        body = event.body.strip()

        # Menü-Freitext-Eingabe hat Priorität vor Commands
        if self._menu.awaiting_text(room.room_id, event.sender):
            await self._menu_handle_text(room.room_id, body, event.sender)
            return

        if not body.startswith("!"):
            return

        parts    = body.split()
        cmd      = parts[0].lower()
        args     = parts[1:]
        room_id  = room.room_id
        is_admin_room = (room.room_id == self.config.admin_room_id)
        is_main_room  = (room.room_id == self.config.room_id)

        # Im Hauptraum keine Commands – nur Vote und Announcements
        if is_main_room:
            return

        try:
            match cmd:
                case "!player":
                    await self._handle_player(args, event.sender, room_id)
                case "!match":
                    await self._handle_match(args, event.sender, room_id)
                case "!result":
                    await self._handle_result(args, event.sender, room_id)
                case "!cmd":
                    if is_admin_room and self._is_admin(event.sender):
                        await self._menu_start(room.room_id, event.sender)
                    elif not self._is_admin(event.sender):
                        await self.send("❌ Keine Berechtigung.", room_id)
                    else:
                        await self.send("ℹ️ `!cmd` funktioniert nur im Admin-Raum.", room_id)
                case "!team":
                    if args and args[0].lower() == "vote":
                        await self._cmd_team(post_vote=True, room_id=room_id)
                    elif args and len(args[0]) == 1 and args[0].upper().isalpha():
                        await self._cmd_team(select_letter=args[0], room_id=room_id)
                    else:
                        await self._cmd_team(room_id=room_id)
                case "!vote":
                    if self._is_admin(event.sender):
                        await self._scheduled_vote()
                    else:
                        await self.send("❌ Keine Berechtigung.", room_id)
                case "!help":
                    await self.send(HELP_TEXT, room_id)
        except Exception as exc:
            logger.exception("Fehler bei Befehl %s", cmd)
            await self.send(f"❌ Fehler: {exc}", room_id)

    async def _on_reaction(self, room, event):
        if event.sender == self.config.user_id:
            return

        content = event.source.get("content", {})

        # ReactionEvent wird von nio nativ geparst – direkt verarbeiten
        if isinstance(event, ReactionEvent):
            target_event_id = event.reacts_to
            key = event.key

            # Admin-Team-Poll?
            if (self._admin_team_poll_id and
                    target_event_id == self._admin_team_poll_id and
                    room.room_id == self.config.admin_room_id):
                await self._handle_admin_team_poll_reaction(room, event)
                return

            # 🔃 → Team wechseln
            if key == "🔃":
                await self._on_team_switch_reaction(room, event)
                return

            # Vote-Reaktionen
            vote = await self.db.get_vote_by_event(target_event_id)
            if not vote or vote["closed"]:
                return

            NUMBER_EMOJIS = {
                "1️⃣": 1, "2️⃣": 2, "3️⃣": 3, "4️⃣": 4, "5️⃣": 5,
                "6️⃣": 6, "7️⃣": 7, "8️⃣": 8, "9️⃣": 9,
            }
            if key in NUMBER_EMOJIS:
                count = NUMBER_EMOJIS[key]
                room_obj = self.client.rooms.get(self.config.room_id)
                sender_display = event.sender
                if room_obj and event.sender in room_obj.users:
                    sender_display = room_obj.users[event.sender].display_name or event.sender
                await self._add_reaction_guests(event.sender, sender_display, count)
            elif key == "🥅":
                await self.db.add_gk_request(vote["id"], event.sender)
                player = await self.db.get_player(event.sender)
                name = player["display_name"] if player else event.sender
                await self.send(f"🥅 **{name}** steht als Torwart zur Verfügung!", self.config.admin_room_id or self.config.room_id)
            elif key == self.config.vote_yes:
                await self.db.upsert_vote_response(vote["id"], event.sender, "yes")
                await self._handle_yes_voter(event.sender)
            elif key == self.config.vote_no:
                await self.db.upsert_vote_response(vote["id"], event.sender, "no")
            return

        # Poll-Responses und Reactions vom poll_sender sind erlaubt –
        # er stimmt ab und reagiert wie jeder andere User.
        # Nur eigene Bot-Events ignorieren (bereits oben gefiltert).

        # Native Matrix-Poll-Antwort (UnknownEvent)
        if event.type in POLL_RESPONSE_TYPES:
            relates_to    = content.get("m.relates_to", {})
            poll_event_id = relates_to.get("event_id")
            answers = next(
                (content.get(k, {}) for k in POLL_RESPONSE_KEYS if k in content),
                {}
            ).get("answers", [])

            # Menü-Poll?
            state = self._menu.get(room.room_id)
            if state and poll_event_id in state.poll_event_ids and event.sender == state.user:
                await self._menu_handle_vote(room.room_id, state, poll_event_id, answers)
                return

            # Proposal-Poll?
            if poll_event_id == self._proposal_poll_id:
                answers = (
                    content.get("org.matrix.msc3381.poll.response", {})
                    or content.get("m.poll.response", {})
                ).get("answers", [])
                for letter in answers:
                    letter = letter.upper()
                    if letter in self._proposals:
                        # Nur eine Stimme pro Nutzer – alte rückgängig machen
                        for l in list(self._proposal_votes):
                            if self._proposal_votes.get(l + "_voter_" + event.sender):
                                self._proposal_votes[l] = max(0, self._proposal_votes.get(l, 0) - 1)
                                del self._proposal_votes[l + "_voter_" + event.sender]
                        self._proposal_votes[letter] = self._proposal_votes.get(letter, 0) + 1
                        self._proposal_votes[letter + "_voter_" + event.sender] = 1
                        logger.info("Proposal vote: %s → %s", event.sender, letter)
                return

            vote = await self.db.get_vote_by_event(poll_event_id)
            if not vote or vote["closed"]:
                # Admin-Team-Poll?
                if poll_event_id == self._admin_team_poll_id:
                    answers = (
                        content.get("org.matrix.msc3381.poll.response", {})
                        or content.get("m.poll.response", {})
                    ).get("answers", [])
                    self._admin_team_selection[event.sender] = answers
                    logger.info("Admin-Poll Auswahl von %s: %s", event.sender, answers)
                return
            answers = (
                content.get("org.matrix.msc3381.poll.response", {})
                or content.get("m.poll.response", {})
            ).get("answers", [])
            if "yes" in answers:
                await self.db.upsert_vote_response(vote["id"], event.sender, "yes")
                await self._handle_yes_voter(event.sender)
            elif "no" in answers:
                await self.db.upsert_vote_response(vote["id"], event.sender, "no")
            return

        # Legacy Emoji-Reaktion (UnknownEvent m.reaction ODER nativer ReactionEvent)
        if isinstance(event, ReactionEvent):
            # nio parsed m.reaction nativ → direkte Attribute
            target_event_id = event.reacts_to
            key = event.key
        elif event.type == "m.reaction":
            relates_to = content.get("m.relates_to", {})
            if relates_to.get("rel_type") != "m.annotation":
                return
            target_event_id = relates_to.get("event_id")
            key = relates_to.get("key", "")
        else:
            return

        # 🔃/🥅/📣/Zahlen auf Admin-Team-Poll → Admin-Aktionen
        if (self._admin_team_poll_id and
                target_event_id == self._admin_team_poll_id and
                room.room_id == self.config.admin_room_id):
            await self._handle_admin_team_poll_reaction(room, event)
            return

        # 🔃 → Team wechseln – funktioniert unabhängig vom Vote-Poll
        if key == "🔃":
            await self._on_team_switch_reaction(room, event)
            return

        # Ab hier nur auf Vote-Events reagieren
        vote = await self.db.get_vote_by_event(target_event_id)
        if not vote or vote["closed"]:
            return

        # Zahlen-Emojis 1️⃣–9️⃣ → Gäste hinzufügen
        NUMBER_EMOJIS = {
            "1️⃣": 1, "2️⃣": 2, "3️⃣": 3, "4️⃣": 4, "5️⃣": 5,
            "6️⃣": 6, "7️⃣": 7, "8️⃣": 8, "9️⃣": 9,
        }
        if key in NUMBER_EMOJIS:
            count = NUMBER_EMOJIS[key]
            room_obj = self.client.rooms.get(self.config.room_id)
            sender_display = event.sender
            if room_obj and event.sender in room_obj.users:
                sender_display = room_obj.users[event.sender].display_name or event.sender
            await self._add_reaction_guests(event.sender, sender_display, count)
            return

        # 🥅 → Als Torwart für diesen Spieltag melden (auch ohne can_gk Flag)
        if key == "🥅":
            await self.db.add_gk_request(vote["id"], event.sender)
            player = await self.db.get_player(event.sender)
            name = player["display_name"] if player else event.sender
            await self.send(f"🥅 **{name}** steht als Torwart zur Verfügung!", self.config.admin_room_id or self.config.room_id)
            return

        if key == self.config.vote_yes:
            await self.db.upsert_vote_response(vote["id"], event.sender, "yes")
            await self._handle_yes_voter(event.sender)
        elif key == self.config.vote_no:
            await self.db.upsert_vote_response(vote["id"], event.sender, "no")

    async def _on_team_switch_reaction(self, room, event):
        """🔃-Reaktion auf die Team-Ankündigung → Spieler wechselt ins andere Team."""
        if not self._has_teams():
            return

        sender = event.sender
        player = None
        team   = None
        for p in self._t1_field:
            if p.get("matrix_id") == sender:
                player, team = p, "t1"
                break
        if not player:
            for p in self._t2_field:
                if p.get("matrix_id") == sender:
                    player, team = p, "t2"
                    break

        if not player:
            return  # Nicht im Team

        own_score = effective_score(player)
        if team == "t1":
            candidates = self._t2_field
            own_list, other_list = self._t1_field, self._t2_field
        else:
            candidates = self._t1_field
            own_list, other_list = self._t2_field, self._t1_field

        if not candidates:
            return

        best = min(candidates, key=lambda p: abs(effective_score(p) - own_score))

        own_list.remove(player)
        other_list.remove(best)
        own_list.append(best)
        other_list.append(player)

        # Alte Team-Nachricht im Hauptraum löschen
        if self._last_team_msg_id:
            await self._redact(self.config.room_id, self._last_team_msg_id)
            self._last_team_msg_id = None

        # Aktualisiertes Team posten und ID merken
        msg = (
            f"🔃 **{player['display_name']}** ↔ **{best['display_name']}**\n\n"
            + self._current_teams_text()
        )
        self._last_team_msg_id = await self.send_main(self._current_teams_text_main())

        # Admin-Poll auch aktualisieren
        if self.config.admin_room_id:
            await self._post_admin_team_poll()

    # ─────────────────────────────────────────────────────────────────────────
    # Vote-Helfer: Auto-Registrierung + Gäste per Reaktion
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_yes_voter(self, matrix_id: str):
        """Prüfe ob Zusagender bereits registriert ist – wenn nicht, anlegen und benachrichtigen."""
        player = await self.db.get_player(matrix_id)
        if player:
            return  # bereits bekannt

        # Anzeigename aus Matrix-Profil holen
        display_name = matrix_id
        room_obj = self.client.rooms.get(self.config.room_id)
        if room_obj and matrix_id in room_obj.users:
            display_name = room_obj.users[matrix_id].display_name or matrix_id

        # Spieler anlegen: Skill 5, kein GK
        player_id = await self.db.add_player(matrix_id, display_name, can_gk=False)
        await self.db.update_score(matrix_id, 5.0)
        logger.info("Neuer Spieler auto-registriert: %s (%s)", display_name, matrix_id)

        # Admin-Benachrichtigung
        if self.config.admin_room_id:
            await self.send(
                f"➕ Neuer Spieler automatisch angelegt:\n"
                f"Name: **{display_name}**\n"
                f"Matrix-ID: `{matrix_id}`\n"
                f"Score: 5.0 | GK: Nein\n"
                f"👋 Willkommensnachricht direkt an Spieler empfohlen.",
                self.config.admin_room_id,
            )

    async def _add_reaction_guests(self, sender: str, sender_display: str, count: int):
        """Füge Gast-Spieler hinzu basierend auf Zahlen-Reaktion auf den Vote-Poll."""
        added = []
        if count == 1:
            name = f"{sender_display}s Gast"
            guest = {
                "id":           f"guest_{sender}_{name.lower().replace(' ', '_')}",
                "matrix_id":    sender,
                "display_name": name,
                "score":        5.0,
                
                "can_gk":       False,
                "is_guest":     True,
            }
            self._guests.append(guest)
            added.append(name)
        else:
            for i in range(1, count + 1):
                name = f"{sender_display}s Gast {i}"
                guest = {
                    "id":           f"guest_{sender}_{i}",
                    "matrix_id":    sender,
                    "display_name": name,
                    "score":        5.0,
                    
                    "can_gk":       False,
                    "is_guest":     True,
                }
                self._guests.append(guest)
                added.append(name)

        names_str = ", ".join(added)
        await self.send(
            f"👤 {count} Gast{'spieler' if count == 1 else 'spieler'} für {sender_display} hinzugefügt: {names_str}",
            self.config.admin_room_id or self.config.room_id,
        )
        logger.info("Gäste via Reaktion hinzugefügt von %s: %s", sender, names_str)

    # ─────────────────────────────────────────────────────────────────────────
    # !player
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_player(self, args: List[str], sender: str, room_id: Optional[str] = None):
        """
        !player              → Spielerliste (öffentlich)
        !player add …        → Spieler anlegen (Admin)
        !player set …        → Score setzen (Admin)
        !player gk …         → GK-Fähigkeit togglen (Admin)
        !player del …        → Spieler deaktivieren (Admin)
        """
        if not args:
            return await self._player_list(room_id=room_id)

        sub = args[0].lower()

        # ── Lesende Unterbefehle (öffentlich) ──────────────────────────────
        # (aktuell nur die Liste, Erweiterung möglich)

        # ── Schreibende Unterbefehle (Admin) ───────────────────────────────
        if not self._is_admin(sender):
            return await self.send("❌ Keine Berechtigung.", room_id)

        if sub == "add":
            # add: Matrix-ID erforderlich, Name optional (wird aus Profil gelesen)
            if len(args) < 2:
                return await self.send(
                    "Syntax: `!player add @user:server [Name] [gk]`\n"
                    "Ohne Namen wird der Matrix-Anzeigename automatisch verwendet."
                , room_id)

            matrix_id = args[1]
            if not matrix_id.startswith("@"):
                return await self.send("❌ Erstes Argument muss eine Matrix-ID sein (beginnt mit @).", room_id)

            rest = args[2:]
            can_gk = "gk" in [a.lower() for a in rest]
            name_parts = [a for a in rest if a.lower() != "gk"]
            name = " ".join(name_parts) if name_parts else await self._get_display_name(matrix_id)

            if await self.db.get_player(matrix_id):
                return await self.send(f"⚠️ `{matrix_id}` existiert bereits.", room_id)
            await self.db.add_player(matrix_id, name, can_gk)
            hint = " 🧤 (Torwart-fähig)" if can_gk else ""
            await self.send(f"✅ **{name}** (`{matrix_id}`){hint} hinzugefügt.", room_id)

        elif sub == "set":
            # !player set @id|Name 7.5           → field (Standard)
            # !player set @id|Name field 7.5     → field
            # !player set @id|Name gk 8.0        → gk
            if len(args) < 3:
                return await self.send(
                    "Syntax: `!player set @user:server|Name [field|gk] Wert`\n"
                    "Ohne Typ-Angabe wird immer `field` gesetzt."
                , room_id)

            if args[2].lower() in ("field", "gk"):
                if len(args) < 4:
                    return await self.send(
                        "Syntax: `!player set @user:server|Name [field|gk] Wert`"
                    , room_id)

                score_type = args[2].lower()
                score_str  = args[3]
            else:
                score_type = "field"
                score_str  = args[2]

            try:
                score = float(score_str)
            except ValueError:
                return await self.send("❌ Score muss eine Zahl zwischen 0 und 10 sein.", room_id)

            p = await self._resolve_player(args[1])
            if not p:
                return await self.send(f"❌ Spieler `{args[1]}` nicht gefunden.", room_id)

            if score_type in ("field", "gk"):
                await self.db.update_score(p["matrix_id"], score)
                await self.send(f"✅ **{p['display_name']}** – Score: **{score:.2f}**", room_id)

        elif sub == "gk":
            # !player gk @id oder Name  →  togglet can_gk
            if len(args) < 2:
                return await self.send("Syntax: `!player gk @user:server` oder `!player gk Name`", room_id)
            p = await self._resolve_player(args[1])
            if not p:
                return await self.send(f"❌ Spieler `{args[1]}` nicht gefunden.", room_id)
            new_val = not bool(p.get("can_gk"))
            await self.db.set_can_gk(p["matrix_id"], new_val)
            status = "🧤 aktiviert" if new_val else "⚽ deaktiviert"
            await self.send(
                f"✅ **{p['display_name']}** – GK-Bevorzugung: **{status}**"
            , room_id)


        elif sub == "del":
            if len(args) < 2:
                return await self.send("Syntax: `!player del @user:server` oder `!player del Name`", room_id)
            p = await self._resolve_player(args[1])
            if not p:
                return await self.send(f"❌ Spieler `{args[1]}` nicht gefunden.", room_id)
            await self.db.deactivate_player(p["matrix_id"])
            await self.send(f"✅ **{p['display_name']}** deaktiviert.", room_id)

        else:
            await self.send(
                f"❌ Unbekannter Unterbefehl `{sub}`.\n"
                "Verfügbar: `add`, `set`, `gk`, `del`"
            , room_id)


    async def _player_list(self, room_id: Optional[str] = None):
        players = await self.db.get_all_players()
        if not players:
            return await self.send("Noch keine Spieler in der Datenbank.", room_id)

        lines = ["**👥 Spieler & Scores**", ""]
        lines.append(f"{'Name':<18} {'Score':>6}  {'Matrix-ID'}")
        lines.append("─" * 50)
        for p in players:
            gk_tag = " 🥅" if p.get("can_gk") else ""
            score = p.get("score", p.get("score_field", 5.0))
            lines.append(
                f"{p['display_name']:<18} "
                f"{score:>6.2f}{gk_tag:<3}  "
                f"`{p['matrix_id']}`"
            )
        await self.send("\n".join(lines), room_id)

    # ─────────────────────────────────────────────────────────────────────────
    # !match
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_match(self, args: List[str], sender: str, room_id: Optional[str] = None):
        """
        !match [N]           → letzte 5 / N Ergebnisse (öffentlich)
        !match change …      → Teams anpassen (Admin)
        !match gk …          → Torwart setzen (Admin)
        !match switched …    → Wertung ein/aus (Admin)
        """
        if not args:
            return await self._match_history(5, room_id=room_id)

        sub = args[0].lower()

        # Zahl → letzte N Ergebnisse
        if sub.isdigit():
            return await self._match_history(int(sub), room_id=room_id)

        # Schreibende Unterbefehle
        if not self._is_admin(sender):
            return await self.send("❌ Keine Berechtigung.", room_id)

        if sub == "change":
            await self._match_change(args[1:], room_id=room_id)
        elif sub == "gk":
            await self._match_setgk(args[1:], room_id=room_id)
        elif sub == "switched":
            await self._match_switched(args[1:], room_id=room_id)
        elif sub == "guest":
            await self._match_guest(args[1:], room_id=room_id)
        else:
            await self.send(
                f"❌ Unbekannter Unterbefehl `{sub}`.\n"
                "Verfügbar: `change`, `gk`, `switched` oder eine Zahl für die Anzahl Ergebnisse."
            , room_id)


    async def _match_history(self, n: int, room_id: Optional[str] = None):
        matches = await self.db.get_last_matches(min(n, 50))
        if not matches:
            return await self.send("📭 Noch keine Ergebnisse gespeichert.", room_id)

        lines = [f"**📋 Letzte {len(matches)} Ergebnisse**", ""]
        for m in matches:
            date = m["played_at"][:10]   # YYYY-MM-DD
            s1   = m["team1_score"]
            s2   = m["team2_score"]
            if s1 > s2:
                result = f"**{s1}:{s2}** 🔴"
            elif s2 > s1:
                result = f"**{s1}:{s2}** 🔵"
            else:
                result = f"**{s1}:{s2}** 🤝"
            lines.append(f"`{date}`  {result}")

        await self.send("\n".join(lines), room_id)

    async def _match_guest(self, args: List[str], room_id: Optional[str] = None):
        """
        !match guest "Name" [Score]
        Fügt einen Gastspieler zur aktuellen Aufstellung hinzu.
        Ohne Score → Durchschnitt der Teamstärken beider Teams.
        Gäste werden nach dem Spiel nicht in der Score-DB gespeichert.
        """
        if not self._has_teams():
            return await self.send("❌ Keine Teams vorhanden. Erst `!team` ausführen.", room_id)
        if not args:
            return await self.send(
                'Syntax: `!match guest "Name" [Score]`\n'
                'Ohne Score → Teamdurchschnitt wird verwendet.'
            , room_id)


        # Name in Anführungszeichen oder einfach erstes Argument
        raw = " ".join(args)
        import re as _re
        m = _re.match(r'^["\'](.+?)["\'](?:\s+([\d.]+))?$', raw)
        if m:
            name  = m.group(1)
            score_str = m.group(2)
        else:
            parts = raw.split()
            name  = parts[0]
            score_str = parts[1] if len(parts) > 1 else None

        # Score bestimmen
        if score_str:
            try:
                score = round(min(10.0, max(0.0, float(score_str))), 2)
            except ValueError:
                return await self.send("❌ Score muss eine Zahl zwischen 0 und 10 sein.", room_id)
        else:
            # Durchschnitt aller aktuellen Feldspieler beider Teams
            all_field = self._t1_field + self._t2_field
            if all_field:
                from teams import effective_score as _eff
                score = round(sum(_eff(p) for p in all_field) / len(all_field), 2)
            else:
                score = 5.0

        # Gastspieler als Dict (keine DB-ID, is_guest=True)
        guest = {
            "id":           f"guest_{name.lower().replace(' ','_')}",
            "matrix_id":    None,
            "display_name": name,
            "score":        score,
            "can_gk":       False,
            "is_guest":     True,
            "active":       1,
        }

        # Ins kleinere Team einfügen (bessere Balance)
        t1_size = len(self._t1_field) + (1 if self._t1_gk else 0)
        t2_size = len(self._t2_field) + (1 if self._t2_gk else 0)
        if t1_size <= t2_size:
            self._t1_field.append(guest)
            team_name = TEAM1_LABEL
        else:
            self._t2_field.append(guest)
            team_name = TEAM2_LABEL

        await self.send(
            f"👤 **{name}** als Gast zu **{team_name}** hinzugefügt "
            f"(Score: {score:.2f}).\n\n"
            + self._current_teams_text()
        , room_id)


    async def _match_change(self, args: List[str], room_id: Optional[str] = None):
        """
        !match change Name1 Name2  → tauschen
        !match change Name         → ins andere Team verschieben
        """
        if not self._has_teams():
            return await self.send("❌ Keine Teams vorhanden. Erst `!team` ausführen.", room_id)
        if not args:
            return await self.send(
                "Syntax: `!match change Name` oder `!match change Name1 Name2`"
            , room_id)


        if len(args) >= 2:
            p1, slot1 = self._find_player(args[0])
            p2, slot2 = self._find_player(args[1])
            if not p1:
                return await self.send(f"❌ **{args[0]}** nicht in den aktuellen Teams.", room_id)
            if not p2:
                return await self.send(f"❌ **{args[1]}** nicht in den aktuellen Teams.", room_id)
            if slot1 == slot2:
                return await self.send(
                    f"⚠️ **{p1['display_name']}** und **{p2['display_name']}** "
                    "sind bereits im selben Team."
                , room_id)

            self._remove_from_slot(p1, slot1)
            self._remove_from_slot(p2, slot2)
            self._add_to_slot(p1, slot2)
            self._add_to_slot(p2, slot1)
            await self.send(
                f"🔄 **{p1['display_name']}** ↔ **{p2['display_name']}** getauscht.\n\n"
                + self._current_teams_text()
            , room_id)

        else:
            p, slot = self._find_player(args[0])
            if not p:
                return await self.send(f"❌ **{args[0]}** nicht in den aktuellen Teams.", room_id)

            target = _opposite_slot(slot)
            self._remove_from_slot(p, slot)

            # War der Spieler GK → schwächsten Feldspieler des alten Teams auto-GK
            if "gk" in slot:
                team = "t1" if "t1" in slot else "t2"
                self._auto_gk_fallback(team)

            self._add_to_slot(p, target)
            team_name = "Team 2 🔵" if "t2" in target else "Team 1 🔴"
            await self.send(
                f"➡️ **{p['display_name']}** → **{team_name}**.\n\n"
                + self._current_teams_text()
            , room_id)


    async def _match_setgk(self, args: List[str], room_id: Optional[str] = None):
        """!match gk Name → Spieler als Torwart seines Teams setzen."""
        if not self._has_teams():
            return await self.send("❌ Keine Teams vorhanden. Erst `!team` ausführen.", room_id)
        if not args:
            return await self.send("Syntax: `!match gk Name`", room_id)

        p, slot = self._find_player(args[0])
        if not p:
            return await self.send(f"❌ **{args[0]}** nicht in den aktuellen Teams.", room_id)

        team = "t1" if "t1" in slot else "t2"
        gk_attr    = f"_{team}_gk"
        field_attr = f"_{team}_field"

        old_gk = getattr(self, gk_attr)
        if old_gk and old_gk["id"] != p["id"]:
            getattr(self, field_attr).append(old_gk)   # alter GK → Feld

        self._remove_from_slot(p, slot)
        setattr(self, gk_attr, p)

        team_label = "Team 1 🔴" if team == "t1" else "Team 2 🔵"
        await self.send(
            f"🧤 **{p['display_name']}** ist Torwart von **{team_label}**.\n\n"
            + self._current_teams_text()
        , room_id)


    async def _match_switched(self, args: List[str], room_id: Optional[str] = None):
        """!match switched Name → Spieler von Wertung aus-/einschließen."""
        if not self._has_teams():
            return await self.send("❌ Keine Teams vorhanden. Erst `!team` ausführen.", room_id)
        if not args:
            return await self.send("Syntax: `!match switched Name`", room_id)

        p, slot = self._find_player(args[0])
        if not p:
            return await self.send(f"❌ **{args[0]}** nicht in den aktuellen Teams.", room_id)

        pid = p["id"]
        if pid in self._switched:
            self._switched.discard(pid)
            await self.send(f"↩️ **{p['display_name']}** – Wertung wieder aktiv.", room_id)
        else:
            self._switched.add(pid)
            await self.send(
                f"🔕 **{p['display_name']}** – keine Score-Wertung nach dem Spiel."
            , room_id)


    # ─────────────────────────────────────────────────────────────────────────
    # !result
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_result(self, args: List[str], sender: str, room_id: Optional[str] = None):
        if not self._is_admin(sender):
            return await self.send("❌ Keine Berechtigung.", room_id)
        if not args:
            return await self.send("Syntax: `!result 3:2`", room_id)
        if not self._has_teams():
            return await self.send("❌ Keine Teams gespeichert. Erst `!team` ausführen.", room_id)

        try:
            s1, s2 = args[0].split(":")
            score1, score2 = int(s1), int(s2)
        except (ValueError, AttributeError):
            return await self.send("❌ Format: `!result 3:2`", room_id)

        all_t1 = self._t1_field + ([self._t1_gk] if self._t1_gk else [])
        all_t2 = self._t2_field + ([self._t2_gk] if self._t2_gk else [])

        # Gäste rausfiltern – haben keine echte DB-ID
        real_t1 = [p for p in all_t1 if not p.get("is_guest")]
        real_t2 = [p for p in all_t2 if not p.get("is_guest")]
        ids1    = [p["id"] for p in real_t1]
        ids2    = [p["id"] for p in real_t2]
        gk1_id  = self._t1_gk["id"] if self._t1_gk and not self._t1_gk.get("is_guest") else None
        gk2_id  = self._t2_gk["id"] if self._t2_gk and not self._t2_gk.get("is_guest") else None

        # Team-Durchschnitte für Elo-Erwartungskorrektur
        from teams import effective_score as _eff
        avg1 = sum(_eff(p) for p in all_t1) / len(all_t1) if all_t1 else 5.0
        avg2 = sum(_eff(p) for p in all_t2) / len(all_t2) if all_t2 else 5.0

        await self.db.save_match(score1, score2, ids1, ids2, gk1_id, gk2_id,
                                  team1_avg=avg1, team2_avg=avg2)

        score_ids = [i for i in (ids1 + ids2) if i not in self._switched]
        await self.db.recalculate_scores(score_ids)

        # Ergebnis-Zeile
        if score1 > score2:
            result_line = f"🏆 **{TEAM1_LABEL} gewinnt!**"
        elif score2 > score1:
            result_line = f"🏆 **{TEAM2_LABEL} gewinnt!**"
        else:
            result_line = "🤝 **Unentschieden!**"

        def roster(field, gk):
            names = ([f"🧤{gk['display_name']}" + (" 👤" if gk.get("is_guest") else "")] if gk else [])
            names += [p["display_name"] + (" 👤" if p.get("is_guest") else "") for p in field]
            return " · ".join(names)

        switched_note = ""
        if self._switched:
            names = []
            for pid in self._switched:
                p = await self.db.get_player_by_id(pid)
                if p:
                    names.append(p["display_name"])
            if names:
                switched_note = f"\n🔕 Ohne Wertung: {', '.join(names)}"

        admin_msg = (
            f"⚽ **Spielergebnis**\n\n"
            f"🟡 {TEAM1_LABEL} **{score1}** – {roster(self._t1_field, self._t1_gk)}\n"
            f"🌈 {TEAM2_LABEL} **{score2}** – {roster(self._t2_field, self._t2_gk)}\n\n"
            f"{result_line}{switched_note}\n\n"
            f"🔄 Scores aktualisiert."
        )
        main_msg = (
            f"⚽ **Spielergebnis**\n\n"
            f"**{TEAM1_LABEL}** 🟡 {score1}:{score2} **{TEAM2_LABEL}** 🌈\n\n"
            f"{result_line}{switched_note}"
        )
        await self.send(admin_msg, room_id)
        if room_id != self.config.room_id:
            await self.send_main(main_msg)


        self._reset_match_state()

    # ─────────────────────────────────────────────────────────────────────────
    # !team  /  !gk  /  !kein_gk
    # ─────────────────────────────────────────────────────────────────────────

    async def _cmd_team(self, select_letter: Optional[str] = None, post_vote: bool = False, room_id: Optional[str] = None):
        """
        !team           → nächsten Vorschlag generieren (oder ersten falls neu)
        !team A/B/…     → Vorschlag aktivieren
        !team vote      → alle Vorschläge zur Abstimmung stellen
        """
        # ── Vorschlag aktivieren ──────────────────────────────────────────
        if select_letter:
            letter = select_letter.upper()
            if letter not in self._proposals:
                return await self.send(
                    f"❌ Vorschlag **{letter}** nicht gefunden.\n"
                    f"Verfügbar: {', '.join(sorted(self._proposals.keys())) or '–'}"
                , room_id)

            self._activate_proposal(letter)
            await self.send(
                f"✅ **Vorschlag {letter}** ist jetzt aktiv.\n\n"
                + self._current_teams_text()
            , room_id)

            return

        # ── Alle Vorschläge zur Abstimmung ────────────────────────────────
        if post_vote:
            if not self._proposals:
                return await self.send("❌ Noch keine Vorschläge. Erst `!team` ausführen.", room_id)
            await self._post_proposal_poll(room_id=room_id)
            return

        # ── Neuen Vorschlag generieren ────────────────────────────────────
        vote = await self.db.get_open_vote()
        if not vote:
            return await self.send(
                "⚠️ Kein offener Vote vorhanden.\n"
                "Admin kann mit `!vote` einen neuen Vote starten."
            , room_id)


        yes_ids = await self.db.get_vote_yes_players(vote["id"])
        if not yes_ids:
            return await self.send("⚠️ Noch keine Zusagen im aktuellen Vote.", room_id)

        players, unknown = [], []
        for mid in yes_ids:
            p = await self.db.get_player(mid)
            if p and p["active"]:
                players.append(p)
            else:
                unknown.append(mid)

        # Gäste dazunehmen
        players += self._guests

        if len(players) < 2:
            return await self.send(
                f"⚠️ Nur {len(players)} bekannte(r) Spieler – mindestens 2 nötig."
            , room_id)


        # Spielerliste geändert? → Vorschläge zurücksetzen
        current_ids = frozenset(
            p.get("id") or p.get("display_name") for p in players
        )
        if current_ids != self._proposal_players:
            self._reset_proposals()
            self._proposal_players = current_ids
            # Gäste nach Reset wieder eintragen
            self._guests = [p for p in players if p.get("is_guest")]

        # Nächsten Buchstaben bestimmen
        used = sorted(self._proposals.keys())
        if not used:
            next_letter = "A"
        elif used[-1] < "Z":
            next_letter = chr(ord(used[-1]) + 1)
        else:
            return await self.send("⚠️ Alle 26 Vorschläge (A–Z) bereits erstellt.", room_id)

        gk_requests = await self.db.get_gk_requests(vote["id"])
        t1f, gk1, t2f, gk2 = build_teams(players, gk_requests)

        self._proposals[next_letter] = (t1f, gk1, t2f, gk2)
        self._activate_proposal(next_letter)

        n_proposals = len(self._proposals)
        hint = ""
        if n_proposals > 1:
            all_letters = ", ".join(f"`!team {l}`" for l in sorted(self._proposals))
            hint = f"\n\n📋 Alle Vorschläge: {all_letters} · Abstimmung: `!team vote`"
        elif n_proposals == 1:
            hint = "\n\nFür einen weiteren Vorschlag nochmal `!team` eingeben."

        msg = f"**Vorschlag {next_letter}**\n\n" + format_teams(t1f, gk1, t2f, gk2) + hint
        if unknown:
            msg += f"\n\n⚠️ Nicht in DB: {', '.join(unknown)}"
        await self.send(msg, room_id)

        # Team auch in Hauptraum ankündigen (ohne Vorschlag-Hinweise)
        if room_id != self.config.room_id:
            main_msg = format_teams_main(t1f, gk1, t2f, gk2)
            self._last_team_msg_id = await self.send_main(main_msg)

        # Admin-Team-Poll in Admin-Gruppe posten
        if self.config.admin_room_id:
            await self._post_admin_team_poll()

    def _activate_proposal(self, letter: str):
        """Setzt einen Vorschlag als aktive Teams."""
        t1f, gk1, t2f, gk2 = self._proposals[letter]
        self._t1_field = list(t1f)
        self._t1_gk    = gk1
        self._t2_field = list(t2f)
        self._t2_gk    = gk2
        self._switched = set()
        self._active_proposal = letter

    async def _cmd_gk(self, sender: str, add: bool, room_id: Optional[str] = None):
        vote = await self.db.get_open_vote()
        if not vote:
            return await self.send("⚠️ Kein offener Vote vorhanden.", room_id)

        yes_ids = await self.db.get_vote_yes_players(vote["id"])
        if sender not in yes_ids:
            return await self.send(
                "⚠️ Du hast noch keine Zusage gegeben. "
                "Erst ✅ im Vote klicken, dann `!gk` schreiben."
            , room_id)


        if add:
            await self.db.add_gk_request(vote["id"], sender)
            p = await self.db.get_player(sender)
            name = p["display_name"] if p else sender
            gk_list = await self.db.get_gk_requests(vote["id"])
            await self.send(
                f"🧤 **{name}** möchte Torwart spielen.\n"
                f"GK-Freiwillige bisher: {len(gk_list)}"
            , room_id)

        else:
            await self.db.remove_gk_request(vote["id"], sender)
            await self.send("👍 GK-Meldung zurückgezogen.", room_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Interaktives Admin-Menü
    # ─────────────────────────────────────────────────────────────────────────

    async def _menu_start(self, room_id: str, sender: str):
        old = self._menu.get(room_id)
        if old:
            await self._menu_redact_all(room_id, old)
        state = self._menu.start(room_id, sender)
        poll_id = await self._post_poll(room_id, main_menu_poll())
        if poll_id:
            state.poll_event_ids.append(poll_id)

    async def _menu_handle_vote(self, room_id: str, state: MenuState, poll_event_id: str, answers: list):
        if not answers:
            return
        answer = answers[0]
        await self._redact(room_id, poll_event_id)
        state.poll_event_ids = [e for e in state.poll_event_ids if e != poll_event_id]

        # ── Level 1: Hauptkategorie ───────────────────────────────────────
        if state.level == 1:
            if answer == "cat_player":
                state.category = "player"
                state.level = 2
                pid = await self._post_poll(room_id, player_menu_poll())
                if pid: state.poll_event_ids.append(pid)

            elif answer == "cat_matchday":
                state.category = "matchday"
                state.level = 2
                vote = await self.db.get_open_vote()
                pid = await self._post_poll(room_id, matchday_menu_poll(bool(vote)))
                if pid: state.poll_event_ids.append(pid)

            elif answer == "cat_team":
                state.category = "team"
                state.level = 2
                pid = await self._post_poll(room_id, team_menu_poll())
                if pid: state.poll_event_ids.append(pid)

        # ── Level 2: Spieler-Untermenü ────────────────────────────────────
        elif state.category == "player" and state.level == 2:
            if answer == "pl_add":
                state.command = "player_add"
                # Raum-Mitglieder holen die noch nicht registriert sind
                room_obj = self.client.rooms.get(room_id)
                all_players = await self.db.get_all_players(active_only=False)
                known_ids = {p["matrix_id"] for p in all_players}
                members = []
                if room_obj:
                    for mid, user in room_obj.users.items():
                        if mid not in known_ids and mid != self.config.user_id:
                            if self.config.poll_sender_id and mid == self.config.poll_sender_id:
                                continue
                            name = user.display_name or mid.split(":")[0].lstrip("@")
                            members.append((mid, name))
                if members:
                    state.level = 2
                    state._members = members  # type: ignore
                    pid = await self._post_poll(room_id, room_members_poll(members))
                    if pid: state.poll_event_ids.append(pid)
                    state.command = "player_add_select"
                else:
                    await self.send("✅ Alle Raum-Mitglieder sind bereits angelegt.", room_id)
                    self._menu.clear(room_id)

            elif answer == "pl_edit":
                state.command = "player_edit_select"
                players = await self.db.get_all_players()
                if not players:
                    await self.send("⚠️ Keine Spieler angelegt.", room_id)
                    self._menu.clear(room_id)
                    return
                state._players = players  # type: ignore
                state.level = 2
                pid = await self._post_poll(room_id, player_select_poll(players, "Score ändern"))
                if pid: state.poll_event_ids.append(pid)

            elif answer == "pl_del":
                state.command = "player_del_select"
                players = await self.db.get_all_players()
                if not players:
                    await self.send("⚠️ Keine Spieler angelegt.", room_id)
                    self._menu.clear(room_id)
                    return
                state._players = players  # type: ignore
                state.level = 2
                pid = await self._post_poll(room_id, player_select_poll(players, "Löschen"))
                if pid: state.poll_event_ids.append(pid)

        # ── Level 2: Spieler-Select-Flows ─────────────────────────────────
        elif state.command == "player_add_select" and answer.startswith("rm_"):
            idx = int(answer[3:])
            members = getattr(state, "_members", [])
            if idx < len(members):
                mid, name = members[idx]
                await self.db.add_player(mid, name)
                await self.db.update_score(mid, 5.0)
                await self.send(f"✅ **{name}** (`{mid}`) hinzugefügt (Score: 5.0).", room_id)
            self._menu.clear(room_id)

        elif state.command == "player_edit_select" and answer.startswith("ps_"):
            player_id = int(answer[3:])
            players = getattr(state, "_players", [])
            p = next((x for x in players if x["id"] == player_id), None)
            if p:
                state.selected_matrix_id = p["matrix_id"]
                state.command = "player_set_score"
                state.level = 2
                pid = await self._post_poll(room_id, score_poll())
                if pid: state.poll_event_ids.append(pid)
                await self.send(f"📊 Score für **{p['display_name']}** wählen:", room_id)
            else:
                self._menu.clear(room_id)

        elif state.command == "player_set_score" and answer.startswith("sc_"):
            score = int(answer[3:]) / 10.0
            mid = state.selected_matrix_id
            if mid:
                p = await self.db.get_player(mid)
                await self.db.update_score(mid, score)
                name = p["display_name"] if p else mid
                await self.send(f"✅ **{name}** – Score: **{score:.1f}**", room_id)
            self._menu.clear(room_id)

        elif state.command == "player_del_select" and answer.startswith("ps_"):
            player_id = int(answer[3:])
            players = getattr(state, "_players", [])
            p = next((x for x in players if x["id"] == player_id), None)
            if p:
                await self.db.deactivate_player(p["matrix_id"])
                await self.send(f"✅ **{p['display_name']}** deaktiviert.", room_id)
            self._menu.clear(room_id)

        # ── Level 2: Matchday-Untermenü ───────────────────────────────────
        elif state.category == "matchday" and state.level == 2:
            if answer == "md_vote":
                await self._scheduled_vote()
                self._menu.clear(room_id)
            elif answer == "md_team":
                await self._cmd_team(room_id=room_id)
                self._menu.clear(room_id)
            elif answer == "md_result":
                state.command = "result"
                state.level = 3
                pid = await self.send("✏️ **Ergebnis eintragen**\nFormat: `3:2`", room_id)
                state.prompt_msg_id = pid

        # ── Level 2: Team-Untermenü ───────────────────────────────────────
        elif state.category == "team" and state.level == 2:
            if answer == "tm_next":
                await self._cmd_team(room_id=room_id)
                self._menu.clear(room_id)
            elif answer == "tm_alt":
                await self._cmd_team(room_id=room_id)
                self._menu.clear(room_id)
            elif answer == "tm_select":
                state.command = "team_select"
                state.level = 3
                avail = ", ".join(sorted(self._proposals.keys())) or "–"
                pid = await self.send(f"✏️ **Vorschlag aktivieren**\nVerfügbar: {avail}\nBuchstabe eingeben:", room_id)
                state.prompt_msg_id = pid
            elif answer == "tm_guest":
                state.command = "match_guest"
                state.level = 3
                pid = await self.send('✏️ **Gastspieler hinzufügen**\nFormat: `"Name"` oder `"Name" 7.5`', room_id)
                state.prompt_msg_id = pid
            elif answer == "tm_change":
                state.command = "match_change"
                state.level = 3
                pid = await self.send("✏️ **Spieler tauschen**\nEin oder zwei Namen:", room_id)
                state.prompt_msg_id = pid
            elif answer == "tm_gk":
                state.command = "match_setgk"
                state.level = 3
                pid = await self.send("✏️ **Torwart setzen**\nName eingeben:", room_id)
                state.prompt_msg_id = pid
            elif answer == "tm_switch":
                state.command = "match_switched"
                state.level = 3
                pid = await self.send("✏️ **Spieler nicht werten**\nName eingeben:", room_id)
                state.prompt_msg_id = pid
            elif answer == "tm_announce":
                if self._has_teams():
                    if self._last_team_msg_id:
                        await self._redact(self.config.room_id, self._last_team_msg_id)
                    self._last_team_msg_id = await self.send_main(self._current_teams_text_main())
                    await self.send("✅ Team angekündigt.", room_id)
                else:
                    await self.send("⚠️ Kein Team aktiv.", room_id)
                self._menu.clear(room_id)

    async def _menu_handle_text(self, room_id: str, text: str, sender: str):
        state = self._menu.get(room_id)
        if not state or state.level != 3:
            return
        if state.prompt_msg_id:
            await self._redact(room_id, state.prompt_msg_id)
        cmd = state.command
        try:
            if cmd == "result":
                await self._handle_result([text.strip()], state.user, room_id)
            elif cmd == "team_select":
                await self._cmd_team(select_letter=text.strip().upper(), room_id=room_id)
            elif cmd == "match_guest":
                await self._match_guest(text.split(), room_id=room_id)
            elif cmd == "match_change":
                await self._match_change(text.split(), room_id=room_id)
            elif cmd == "match_setgk":
                await self._match_setgk([text.strip()], room_id=room_id)
            elif cmd == "match_switched":
                await self._match_switched([text.strip()], room_id=room_id)
        except Exception as exc:
            logger.exception("Menü-Fehler cmd=%s", cmd)
            await self.send(f"❌ Fehler: {exc}", room_id)
        self._menu.clear(room_id)

    async def _menu_player_add(self, room_id: str, text: str, sender: str):
        """
        Spieler hinzufügen – Name optional.
        Wenn nur Matrix-ID → Display Name aus Matrix-Profil holen.
        Wenn kein @ → als Name + eigene Matrix-ID des Senders verwenden?
        Nein: Text ist matrix_id oder Name.
        Wenn text starts with @ → Matrix-ID, Name aus Profil.
        Sonst → Name, Matrix-ID muss noch eingegeben werden (Sender-ID fallback).
        """
        text = text.strip()
        if text.startswith("@") and ":" in text:
            matrix_id = text.split()[0]
            # Optionales 'gk' am Ende
            can_gk = "gk" in text.lower().split()[1:] if len(text.split()) > 1 else False
            # Display Name aus Matrix-Profil holen
            display_name = await self._get_display_name(matrix_id)
            if await self.db.get_player(matrix_id):
                return await self.send(f"⚠️ `{matrix_id}` existiert bereits.", room_id)
            await self.db.add_player(matrix_id, display_name, can_gk)
            hint = " 🧤" if can_gk else ""
            await self.send(f"✅ **{display_name}** (`{matrix_id}`){hint} hinzugefügt.", room_id)
        else:
            # Name angegeben, keine Matrix-ID → Fehler mit Hinweis
            await self.send(
                "❌ Bitte Matrix-ID angeben (beginnt mit @).\n"
                "Beispiel: `@max:matrix.srz.one` oder `@max:matrix.srz.one gk`",
                room_id
            )

    async def _get_display_name(self, matrix_id: str) -> str:
        """Display Name aus Matrix-Profil holen, Fallback auf Localpart."""
        try:
            resp = await self.client.get_displayname(matrix_id)
            if hasattr(resp, "displayname") and resp.displayname:
                return resp.displayname
        except Exception:
            pass
        # Fallback: @localpart:server → localpart
        return matrix_id.split(":")[0].lstrip("@")

    async def _menu_redact_all(self, room_id: str, state):
        """Alle offenen Poll-Nachrichten des States löschen."""
        for eid in state.poll_event_ids:
            await self._redact(room_id, eid)
        if state.prompt_msg_id:
            await self._redact(room_id, state.prompt_msg_id)

    async def _redact(self, room_id: str, event_id: str):
        """Matrix-Nachricht löschen (redact)."""
        try:
            await self.client.room_redact(room_id, event_id, reason="Menü-Auswahl")
        except Exception as exc:
            logger.warning("Redact fehlgeschlagen %s: %s", event_id, exc)

    async def _post_poll(self, room_id: str, content: dict) -> Optional[str]:
        """Poll posten – über poll_client falls konfiguriert, sonst Bot."""
        client = self.poll_client if self.poll_client else self.client
        resp = await client.room_send(room_id, POLL_EVENT_TYPE, content)
        if isinstance(resp, RoomSendResponse):
            logger.info("Poll gesendet als %s", client.user_id)
            return resp.event_id
        logger.error("Poll fehlgeschlagen: %s", resp)
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Scheduled jobs
    # ─────────────────────────────────────────────────────────────────────────

    async def _scheduled_vote(self):
        now        = datetime.now()
        days_ahead = (6 - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        if now.weekday() == 5:   # Samstag → Spiel morgen
            days_ahead = 1
        game_date     = now + timedelta(days=days_ahead)
        game_date_str = game_date.strftime("%d.%m.%Y")
        cfg           = self.config

        title = (
            f"Kicken Sonntag, {game_date_str} um "
            f"{cfg.game_hour:02d}:{cfg.game_minute:02d} Uhr"
        )

        poll_content = make_poll(
            f"⚽ {title}",
            [("yes", "✅ Dabei"), ("no", "❌ Nicht dabei")],
        )

        event_id = await self._post_poll(self.config.room_id, poll_content)
        if event_id:
            vote_date = game_date.strftime("%Y-%m-%d")
            vote_id   = await self.db.create_vote(event_id, vote_date)
            logger.info("Poll gestartet – event_id=%s vote_id=%d", event_id, vote_id)
            self._reset_proposals()
            self._guests = []  # Gästeliste bei neuem Vote zurücksetzen
        else:
            logger.error("Poll konnte nicht gepostet werden")

    async def _scheduled_teams(self):
        logger.info("Automatische Team-Generierung ausgelöst")
        await self._cmd_team(room_id=self.config.admin_room_id)

    async def _scheduled_apply_voted_proposal(self):
        """Sonntag 10:00 – meistgewählten Vorschlag aktivieren."""
        if not self._proposal_votes or not self._proposals:
            logger.info("Kein Proposal-Vote vorhanden – nichts zu tun")
            return

        # Nur echte Stimmen (keine _voter_ Tracker-Keys)
        real_votes = {k: v for k, v in self._proposal_votes.items()
                      if len(k) == 1 and k in self._proposals}
        if not real_votes:
            return

        winner = max(real_votes, key=lambda k: real_votes[k])
        self._activate_proposal(winner)
        votes_summary = " · ".join(
            f"{l}: {real_votes.get(l, 0)} Stimme(n)"
            for l in sorted(self._proposals)
        )
        await self.send(
            f"🗳️ **Abstimmungsergebnis**\n\n"
            f"{votes_summary}\n\n"
            f"✅ **Vorschlag {winner}** wurde aktiviert!\n\n"
            + self._current_teams_text()
        )

    async def _post_proposal_poll(self, room_id: Optional[str] = None):
        """Alle Vorschläge als Matrix-Poll zur Abstimmung stellen."""
        letters = sorted(self._proposals.keys())

        # Kurze Vorschau pro Option
        preview_lines = []
        for l in letters:
            t1f, gk1, t2f, gk2 = self._proposals[l]
            gk1_name = f"🧤{gk1['display_name']}" if gk1 else "–"
            gk2_name = f"🧤{gk2['display_name']}" if gk2 else "–"
            t1_names = gk1_name + " " + " ".join(p["display_name"] for p in t1f)
            t2_names = gk2_name + " " + " ".join(p["display_name"] for p in t2f)
            preview_lines.append(
                f"{l}: 🟡 {t1_names.strip()}  vs  🌈 {t2_names.strip()}"
            )

        preview = "\n".join(preview_lines)

        poll_content = make_poll(
            "Welche Mannschaftsaufteilung?",
            [(l, f"Vorschlag {l}") for l in letters],
        )

        event_id = await self._post_poll(self.config.room_id, poll_content)
        if event_id:
            self._proposal_poll_id = event_id
            self._proposal_votes   = {}
            await self.send(
                f"📋 Vorschläge zur Abstimmung:\n{preview}\n\n"
                f"Um 10:00 Uhr wird der meistgewählte Vorschlag aktiviert.\n"
                f"Oder jetzt wählen: `!team A`, `!team B`, …"
            , room_id)
        else:
            logger.error("Proposal poll konnte nicht gepostet werden")

    async def _post_admin_team_poll(self):
        """Aktuelles Team als Poll in der Admin-Gruppe posten. Spieler sind die Antworten."""
        if not self.config.admin_room_id:
            return
        if not self._has_teams():
            return

        # Alten Admin-Poll löschen
        if self._admin_team_poll_id:
            await self._redact(self.config.admin_room_id, self._admin_team_poll_id)
            self._admin_team_poll_id = None

        # Alle Spieler sammeln mit Team-Zuordnung
        all_players = []
        for p in self._t1_field:
            all_players.append((p, "🟡"))
        if self._t1_gk:
            all_players.append((self._t1_gk, "🟡🧤"))
        for p in self._t2_field:
            all_players.append((p, "🌈"))
        if self._t2_gk:
            all_players.append((self._t2_gk, "🌈🧤"))

        # Poll-Antworten: eine pro Spieler
        answers = []
        self._admin_team_poll_map = {}
        for i, (p, team_icon) in enumerate(all_players):
            aid = f"p{i}"
            label = f"{team_icon} {p['display_name']}"
            answers.append((aid, label))
            self._admin_team_poll_map[aid] = p

        self._admin_team_selection = {}

        poll_content = make_poll(
            "🏃 Team-Bearbeitung: Spieler auswählen, dann reagieren:\n"
            "🔃 = Team wechseln  |  🥅 = Torwart  |  1️⃣–9️⃣ = Gäste hinzufügen\n"
            "📣 = Team in Hauptgruppe ankündigen",
            answers,
            max_selections=len(answers),
        )

        event_id = await self._post_poll(self.config.admin_room_id, poll_content)
        if event_id:
            self._admin_team_poll_id = event_id
            logger.info("Admin-Team-Poll gepostet: %s", event_id)

    async def _handle_admin_team_poll_reaction(self, room, event):
        """Reaktion auf den Admin-Team-Poll verarbeiten."""
        if not self.config.admin_room_id:
            return
        if room.room_id != self.config.admin_room_id:
            return
        if not self._is_admin(event.sender):
            return

        content = event.source.get("content", {})
        relates_to = content.get("m.relates_to", {})
        if relates_to.get("rel_type") != "m.annotation":
            return
        if relates_to.get("event_id") != self._admin_team_poll_id:
            return

        key = relates_to.get("key", "")
        sender = event.sender

        # Ausgewählte Spieler aus Poll-Responses holen
        selected = self._admin_team_selection.get(sender, [])
        players = [self._admin_team_poll_map[aid] for aid in selected if aid in self._admin_team_poll_map]

        NUMBER_EMOJIS = {"1️⃣": 1, "2️⃣": 2, "3️⃣": 3, "4️⃣": 4, "5️⃣": 5,
                         "6️⃣": 6, "7️⃣": 7, "8️⃣": 8, "9️⃣": 9}

        if key == "🔃":
            if not players:
                await self.send("⚠️ Erst Spieler im Poll auswählen, dann reagieren.", self.config.admin_room_id)
                return
            for p in players:
                self._switch_player_team(p)
            names = ", ".join(p["display_name"] for p in players)
            await self.send(f"🔃 Team gewechselt: {names}", self.config.admin_room_id)
            if self._last_team_msg_id:
                await self._redact(self.config.room_id, self._last_team_msg_id)
            self._last_team_msg_id = await self.send_main(self._current_teams_text_main())
            await self._post_admin_team_poll()

        elif key == "🥅":
            if not players:
                await self.send("⚠️ Erst Spieler im Poll auswählen, dann reagieren.", self.config.admin_room_id)
                return
            for p in players:
                self._set_player_gk(p)
            names = ", ".join(p["display_name"] for p in players)
            await self.send(f"🥅 Als Torwart gesetzt: {names}", self.config.admin_room_id)
            if self._last_team_msg_id:
                await self._redact(self.config.room_id, self._last_team_msg_id)
            self._last_team_msg_id = await self.send_main(self._current_teams_text_main())
            await self._post_admin_team_poll()

        elif key in NUMBER_EMOJIS:
            count = NUMBER_EMOJIS[key]
            p = await self.db.get_player(sender)
            sender_display = p["display_name"] if p else sender
            await self._add_guests_balanced(sender, sender_display, count)
            if self._last_team_msg_id:
                await self._redact(self.config.room_id, self._last_team_msg_id)
            self._last_team_msg_id = await self.send_main(self._current_teams_text_main())
            await self._post_admin_team_poll()

        elif key == "📣":
            if self._last_team_msg_id:
                await self._redact(self.config.room_id, self._last_team_msg_id)
            self._last_team_msg_id = await self.send_main(self._current_teams_text_main())
            await self.send("✅ Team wurde in der Hauptgruppe angekündigt.", self.config.admin_room_id)

    def _switch_player_team(self, player: Dict):
        """Spieler ins andere Team wechseln (ohne Neuberechnung)."""
        if player in self._t1_field:
            self._t1_field.remove(player)
            self._t2_field.append(player)
        elif player in self._t2_field:
            self._t2_field.remove(player)
            self._t1_field.append(player)
        elif self._t1_gk and self._t1_gk["id"] == player["id"]:
            self._t1_gk = None
            self._t2_gk = player
        elif self._t2_gk and self._t2_gk["id"] == player["id"]:
            self._t2_gk = None
            self._t1_gk = player

    def _set_player_gk(self, player: Dict):
        """Spieler als Torwart setzen (ohne Neuberechnung). Tauscht ggf. alten TW ins Feld."""
        # Spieler aus aktuellem Slot entfernen
        team = None
        if player in self._t1_field:
            self._t1_field.remove(player)
            team = "t1"
        elif player in self._t2_field:
            self._t2_field.remove(player)
            team = "t2"
        elif self._t1_gk and self._t1_gk["id"] == player["id"]:
            return  # Bereits TW Team 1
        elif self._t2_gk and self._t2_gk["id"] == player["id"]:
            return  # Bereits TW Team 2

        if team == "t1":
            if self._t1_gk:
                self._t1_field.append(self._t1_gk)
            self._t1_gk = player
        elif team == "t2":
            if self._t2_gk:
                self._t2_field.append(self._t2_gk)
            self._t2_gk = player

    async def _add_guests_balanced(self, sender: str, sender_display: str, count: int):
        """Gäste hinzufügen und fair auf beide Teams verteilen."""
        for i in range(1, count + 1):
            name = f"{sender_display}s Gast" if count == 1 else f"{sender_display}s Gast {i}"
            guest = {
                "id":           f"guest_{sender}_{i}_{len(self._guests)}",
                "matrix_id":    sender,
                "display_name": name,
                "score":        5.0,
                
                "can_gk":       False,
                "is_guest":     True,
            }
            self._guests.append(guest)
            # Fair verteilen: kleineres Team bekommt den Gast
            t1_size = len(self._t1_field) + (1 if self._t1_gk else 0)
            t2_size = len(self._t2_field) + (1 if self._t2_gk else 0)
            if t1_size <= t2_size:
                self._t1_field.append(guest)
            else:
                self._t2_field.append(guest)

        names = ", ".join(
            f"{sender_display}s Gast" if count == 1
            else f"{sender_display}s Gast {i}"
            for i in range(1, count + 1)
        )
        await self.send(f"👤 {count} Gast(spieler) hinzugefügt: {names}", self.config.admin_room_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Team-Slot-Hilfsmethoden
    # ─────────────────────────────────────────────────────────────────────────

    def _find_player(self, name: str) -> Tuple[Optional[Dict], str]:
        """Suche Spieler per Anzeigename (case-insensitive) in den aktuellen Teams."""
        name_l = name.lower()
        for p in self._t1_field:
            if p["display_name"].lower() == name_l:
                return p, "t1_field"
        for p in self._t2_field:
            if p["display_name"].lower() == name_l:
                return p, "t2_field"
        if self._t1_gk and self._t1_gk["display_name"].lower() == name_l:
            return self._t1_gk, "t1_gk"
        if self._t2_gk and self._t2_gk["display_name"].lower() == name_l:
            return self._t2_gk, "t2_gk"
        return None, ""

    def _remove_from_slot(self, player: Dict, slot: str):
        if slot == "t1_field":
            self._t1_field = [p for p in self._t1_field if p["id"] != player["id"]]
        elif slot == "t2_field":
            self._t2_field = [p for p in self._t2_field if p["id"] != player["id"]]
        elif slot == "t1_gk":
            if self._t1_gk and self._t1_gk["id"] == player["id"]:
                self._t1_gk = None
        elif slot == "t2_gk":
            if self._t2_gk and self._t2_gk["id"] == player["id"]:
                self._t2_gk = None

    def _add_to_slot(self, player: Dict, slot: str):
        if slot == "t1_field":
            self._t1_field.append(player)
        elif slot == "t2_field":
            self._t2_field.append(player)
        elif slot == "t1_gk":
            if self._t1_gk:
                self._t1_field.append(self._t1_gk)
            self._t1_gk = player
        elif slot == "t2_gk":
            if self._t2_gk:
                self._t2_field.append(self._t2_gk)
            self._t2_gk = player

    def _auto_gk_fallback(self, team: str):
        """Setze schwächsten Spieler (effective_score) des Teams automatisch als GK."""
        field_attr = f"_{team}_field"
        gk_attr    = f"_{team}_gk"
        field      = getattr(self, field_attr)
        if not field:
            return
        fallback = min(field, key=effective_score)
        setattr(self, field_attr, [p for p in field if p["id"] != fallback["id"]])
        setattr(self, gk_attr, fallback)


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def _opposite_slot(slot: str) -> str:
    return {"t1_field": "t2_field", "t2_field": "t1_field",
            "t1_gk": "t2_gk",       "t2_gk":    "t1_gk"}.get(slot, "t2_field")


def _load_sync_token() -> Optional[str]:
    """Gespeicherten Sync-Token laden."""
    try:
        with open(SYNC_TOKEN_PATH, "r") as f:
            token = f.read().strip()
            return token if token else None
    except FileNotFoundError:
        return None


def _save_sync_token(token: str):
    """Sync-Token auf Disk schreiben (atomar)."""
    os.makedirs(os.path.dirname(SYNC_TOKEN_PATH), exist_ok=True)
    tmp = SYNC_TOKEN_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write(token)
    os.replace(tmp, SYNC_TOKEN_PATH)


def _md_to_html(text: str) -> str:
    import re
    text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.*?)`",       r"<code>\1</code>",     text)
    text = text.replace("\n", "<br/>")
    return text
