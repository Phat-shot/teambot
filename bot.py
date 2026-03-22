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
    RoomMessageText,
    RoomSendResponse,
    SyncResponse,
    UnknownEvent,
)

from config import Config
from db import Database
from teams import build_teams, format_teams, effective_score

SYNC_TOKEN_PATH = "data/sync_token"
MAX_EVENT_AGE_SECONDS = 7 * 24 * 3600   # 1 Woche

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
HELP_TEXT = """\
**⚽ TeamBot – Befehle**

**Für alle**
`!player`        – Spielerliste mit Scores und Matrix-ID
`!match [N]`     – Letzte 5 (oder N) Ergebnisse
`!gk`            – Als Torwart für dieses Spiel melden
`!kein_gk`       – GK-Meldung zurückziehen
`!team`          – Teams aus dem aktuellen Vote generieren
`!help`          – Diese Hilfe

**Admin – Spieler-Stammdaten** _(Name oder @user:server möglich)_
`!player add @user:server Name`      – Spieler anlegen
`!player add @user:server Name gk`   – Spieler anlegen (Torwart-fähig)
`!player set Name 7.5`               – Feldspieler-Score setzen
`!player set Name field 7.5`         – Feldspieler-Score setzen (explizit)
`!player set Name gk 8.0`            – Torwart-Score setzen
`!player gk Name`                    – GK-Fähigkeit ein/aus (Score bleibt)
`!player del Name`                   – Spieler deaktivieren

**Admin – Aktuelles Spiel** _(Name oder @user:server möglich)_
`!match change Name1 Name2`  – Zwei Spieler tauschen
`!match change Name`         – Spieler ins andere Team verschieben
`!match gk Name`             – Spieler als Torwart seines Teams setzen
`!match switched Name`       – Score-Wertung ein-/ausschalten (Toggle)

**Admin – Ergebnis & Vote**
`!result 3:2`  – Ergebnis + Score-Update
`!vote`        – Vote sofort starten

**Score-System**
Feldspieler: `field`-Score · GK-fähige Spieler: `0,5 × field + 0,5 × gk`
Neuberechnung: 50 % Basis · 30 % letzte 5 Spiele · 20 % letztes Spiel

**Torwart-Zuweisung**
① Freiwillige (`!gk`) nach GK-Score
② GK-fähige Spieler nach GK-Score
③ Fallback: schwächster Spieler pro Team
"""


class TeamBot:
    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.db_path)
        self.client = AsyncClient(config.homeserver, config.user_id)
        self.scheduler = AsyncIOScheduler(timezone="Europe/Berlin")

        # Aktuell generierte Teams (für Korrekturen und !result)
        self._t1_field:   List[Dict]    = []
        self._t1_gk:      Optional[Dict] = None
        self._t2_field:   List[Dict]    = []
        self._t2_gk:      Optional[Dict] = None
        self._switched:   set           = set()   # player IDs ohne Wertung

    # ─────────────────────────────────────────────────────────────────────────
    # Start
    # ─────────────────────────────────────────────────────────────────────────

    async def start(self):
        await self.db.connect()

        resp = await self.client.login(self.config.password)
        if not isinstance(resp, LoginResponse):
            raise RuntimeError(f"Matrix-Login fehlgeschlagen: {resp}")
        logger.info("Eingeloggt als %s", self.config.user_id)

        self.client.add_event_callback(self._on_message, RoomMessageText)
        self.client.add_event_callback(self._on_reaction, UnknownEvent)
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
            sync_filter=_build_sync_filter(),
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

    # ─────────────────────────────────────────────────────────────────────────
    # Hilfsmethoden
    # ─────────────────────────────────────────────────────────────────────────

    def _is_admin(self, matrix_id: str) -> bool:
        return matrix_id in self.config.admin_users

    async def _resolve_player(self, query: str) -> Optional[Dict]:
        """
        Spieler per Matrix-ID (@user:server) oder Anzeigenamen finden.
        Gibt None zurück wenn nicht gefunden.
        """
        return await self.db.find_player(query)

    def _has_teams(self) -> bool:
        return bool(self._t1_field or self._t1_gk or self._t2_field or self._t2_gk)

    def _reset_match_state(self):
        self._t1_field = []
        self._t1_gk    = None
        self._t2_field = []
        self._t2_gk    = None
        self._switched = set()

    def _current_teams_text(self) -> str:
        return format_teams(self._t1_field, self._t1_gk, self._t2_field, self._t2_gk)

    async def send(self, text: str) -> Optional[str]:
        content = {
            "msgtype": "m.text",
            "body": text,
            "format": "org.matrix.custom.html",
            "formatted_body": _md_to_html(text),
        }
        resp = await self.client.room_send(
            self.config.room_id, "m.room.message", content
        )
        if isinstance(resp, RoomSendResponse):
            return resp.event_id
        logger.error("send() fehlgeschlagen: %s", resp)
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
        if room.room_id != self.config.room_id:
            return
        if event.sender == self.config.user_id:
            return

        # Events älter als 1 Woche ignorieren (Schutz nach Neustart)
        age_ms = getattr(event, "age", None)
        if age_ms is None:
            # server_timestamp ist Unix-ms
            ts = getattr(event, "server_timestamp", 0)
            age_ms = (datetime.now().timestamp() * 1000) - ts
        if age_ms > MAX_EVENT_AGE_SECONDS * 1000:
            logger.debug("Event übersprungen (zu alt: %.0fs): %s", age_ms/1000, event.event_id)
            return

        body = event.body.strip()
        if not body.startswith("!"):
            return

        parts = body.split()
        cmd   = parts[0].lower()
        args  = parts[1:]

        try:
            match cmd:
                case "!player":
                    await self._handle_player(args, event.sender)
                case "!match":
                    await self._handle_match(args, event.sender)
                case "!result":
                    await self._handle_result(args, event.sender)
                case "!team":
                    await self._cmd_team()
                case "!vote":
                    if self._is_admin(event.sender):
                        await self._scheduled_vote()
                    else:
                        await self.send("❌ Keine Berechtigung.")
                case "!gk":
                    await self._cmd_gk(event.sender, add=True)
                case "!kein_gk":
                    await self._cmd_gk(event.sender, add=False)
                case "!help":
                    await self.send(HELP_TEXT)
        except Exception as exc:
            logger.exception("Fehler bei Befehl %s", cmd)
            await self.send(f"❌ Fehler: {exc}")

    async def _on_reaction(self, room, event):
        if room.room_id != self.config.room_id:
            return
        if event.sender == self.config.user_id:
            return

        content = event.source.get("content", {})

        # Native Matrix-Poll-Antwort (MSC3381)
        if event.type in ("org.matrix.msc3381.poll.response", "m.poll.response"):
            relates_to    = content.get("m.relates_to", {})
            poll_event_id = relates_to.get("event_id")
            vote = await self.db.get_vote_by_event(poll_event_id)
            if not vote or vote["closed"]:
                return
            answers = (
                content.get("org.matrix.msc3381.poll.response", {})
                or content.get("m.poll.response", {})
            ).get("answers", [])
            if "yes" in answers:
                await self.db.upsert_vote_response(vote["id"], event.sender, "yes")
            elif "no" in answers:
                await self.db.upsert_vote_response(vote["id"], event.sender, "no")
            return

        # Legacy Emoji-Reaktion
        if event.type != "m.reaction":
            return
        relates_to = content.get("m.relates_to", {})
        if relates_to.get("rel_type") != "m.annotation":
            return
        target_event_id = relates_to.get("event_id")
        key  = relates_to.get("key", "")
        vote = await self.db.get_vote_by_event(target_event_id)
        if not vote or vote["closed"]:
            return
        if key == self.config.vote_yes:
            await self.db.upsert_vote_response(vote["id"], event.sender, "yes")
        elif key == self.config.vote_no:
            await self.db.upsert_vote_response(vote["id"], event.sender, "no")

    # ─────────────────────────────────────────────────────────────────────────
    # !player
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_player(self, args: List[str], sender: str):
        """
        !player              → Spielerliste (öffentlich)
        !player add …        → Spieler anlegen (Admin)
        !player set …        → Score setzen (Admin)
        !player gk …         → GK-Fähigkeit togglen (Admin)
        !player del …        → Spieler deaktivieren (Admin)
        """
        if not args:
            return await self._player_list()

        sub = args[0].lower()

        # ── Lesende Unterbefehle (öffentlich) ──────────────────────────────
        # (aktuell nur die Liste, Erweiterung möglich)

        # ── Schreibende Unterbefehle (Admin) ───────────────────────────────
        if not self._is_admin(sender):
            return await self.send("❌ Keine Berechtigung.")

        if sub == "add":
            # add benötigt immer Matrix-ID (neuer Spieler, Name noch nicht in DB)
            if len(args) < 3:
                return await self.send(
                    "Syntax: `!player add @user:server Name [gk]`\n"
                    "`gk` am Ende = Spieler kann Torwart spielen.\n"
                    "Hinweis: `add` benötigt immer die Matrix-ID."
                )
            matrix_id = args[1]
            name      = args[2]
            can_gk    = len(args) > 3 and args[3].lower() == "gk"

            if await self.db.get_player(matrix_id):
                return await self.send(f"⚠️ `{matrix_id}` existiert bereits.")
            await self.db.add_player(matrix_id, name, can_gk)
            hint = " 🧤 (Torwart-fähig)" if can_gk else ""
            await self.send(f"✅ **{name}** hinzugefügt{hint}.")

        elif sub == "set":
            # !player set @id|Name 7.5           → field (Standard)
            # !player set @id|Name field 7.5     → field
            # !player set @id|Name gk 8.0        → gk
            if len(args) < 3:
                return await self.send(
                    "Syntax: `!player set @user:server|Name [field|gk] Wert`\n"
                    "Ohne Typ-Angabe wird immer `field` gesetzt."
                )
            if args[2].lower() in ("field", "gk"):
                if len(args) < 4:
                    return await self.send(
                        "Syntax: `!player set @user:server|Name [field|gk] Wert`"
                    )
                score_type = args[2].lower()
                score_str  = args[3]
            else:
                score_type = "field"
                score_str  = args[2]

            try:
                score = float(score_str)
            except ValueError:
                return await self.send("❌ Score muss eine Zahl zwischen 0 und 10 sein.")

            p = await self._resolve_player(args[1])
            if not p:
                return await self.send(f"❌ Spieler `{args[1]}` nicht gefunden.")

            if score_type == "field":
                await self.db.update_field_score(p["matrix_id"], score)
                await self.send(f"✅ **{p['display_name']}** – Feld: **{score:.2f}**")
            else:
                await self.db.update_gk_score(p["matrix_id"], score)
                await self.send(f"✅ **{p['display_name']}** – Torwart: **{score:.2f}**")

        elif sub == "gk":
            # !player gk @id oder Name  →  togglet can_gk
            if len(args) < 2:
                return await self.send("Syntax: `!player gk @user:server` oder `!player gk Name`")
            p = await self._resolve_player(args[1])
            if not p:
                return await self.send(f"❌ Spieler `{args[1]}` nicht gefunden.")
            new_val = not bool(p.get("can_gk"))
            await self.db.set_can_gk(p["matrix_id"], new_val)
            status = "🧤 aktiviert" if new_val else "⚽ deaktiviert"
            await self.send(
                f"✅ **{p['display_name']}** – GK-Fähigkeit: **{status}**\n"
                f"GK-Score bleibt: {p.get('score_gk', 5.0):.2f}"
            )

        elif sub == "del":
            if len(args) < 2:
                return await self.send("Syntax: `!player del @user:server` oder `!player del Name`")
            p = await self._resolve_player(args[1])
            if not p:
                return await self.send(f"❌ Spieler `{args[1]}` nicht gefunden.")
            await self.db.deactivate_player(p["matrix_id"])
            await self.send(f"✅ **{p['display_name']}** deaktiviert.")

        else:
            await self.send(
                f"❌ Unbekannter Unterbefehl `{sub}`.\n"
                "Verfügbar: `add`, `set`, `gk`, `del`"
            )

    async def _player_list(self):
        players = await self.db.get_all_players()
        if not players:
            return await self.send("Noch keine Spieler in der Datenbank.")

        lines = ["**👥 Spieler & Scores**", ""]
        lines.append(f"{'Name':<18} {'Feld':>6}  {'GK':>6}  {'Matrix-ID'}")
        lines.append("─" * 60)
        for p in players:
            gk_tag = " 🧤" if p.get("can_gk") else ""
            lines.append(
                f"{p['display_name']:<18} "
                f"{p.get('score_field', 5.0):>6.2f}  "
                f"{p.get('score_gk', 5.0):>6.2f}{gk_tag:<3}  "
                f"`{p['matrix_id']}`"
            )
        await self.send("\n".join(lines))

    # ─────────────────────────────────────────────────────────────────────────
    # !match
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_match(self, args: List[str], sender: str):
        """
        !match [N]           → letzte 5 / N Ergebnisse (öffentlich)
        !match change …      → Teams anpassen (Admin)
        !match gk …          → Torwart setzen (Admin)
        !match switched …    → Wertung ein/aus (Admin)
        """
        if not args:
            return await self._match_history(5)

        sub = args[0].lower()

        # Zahl → letzte N Ergebnisse
        if sub.isdigit():
            return await self._match_history(int(sub))

        # Schreibende Unterbefehle
        if not self._is_admin(sender):
            return await self.send("❌ Keine Berechtigung.")

        if sub == "change":
            await self._match_change(args[1:])
        elif sub == "gk":
            await self._match_setgk(args[1:])
        elif sub == "switched":
            await self._match_switched(args[1:])
        else:
            await self.send(
                f"❌ Unbekannter Unterbefehl `{sub}`.\n"
                "Verfügbar: `change`, `gk`, `switched` oder eine Zahl für die Anzahl Ergebnisse."
            )

    async def _match_history(self, n: int):
        matches = await self.db.get_last_matches(min(n, 50))
        if not matches:
            return await self.send("📭 Noch keine Ergebnisse gespeichert.")

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

        await self.send("\n".join(lines))

    async def _match_change(self, args: List[str]):
        """
        !match change Name1 Name2  → tauschen
        !match change Name         → ins andere Team verschieben
        """
        if not self._has_teams():
            return await self.send("❌ Keine Teams vorhanden. Erst `!team` ausführen.")
        if not args:
            return await self.send(
                "Syntax: `!match change Name` oder `!match change Name1 Name2`"
            )

        if len(args) >= 2:
            p1, slot1 = self._find_player(args[0])
            p2, slot2 = self._find_player(args[1])
            if not p1:
                return await self.send(f"❌ **{args[0]}** nicht in den aktuellen Teams.")
            if not p2:
                return await self.send(f"❌ **{args[1]}** nicht in den aktuellen Teams.")
            if slot1 == slot2:
                return await self.send(
                    f"⚠️ **{p1['display_name']}** und **{p2['display_name']}** "
                    "sind bereits im selben Team."
                )
            self._remove_from_slot(p1, slot1)
            self._remove_from_slot(p2, slot2)
            self._add_to_slot(p1, slot2)
            self._add_to_slot(p2, slot1)
            await self.send(
                f"🔄 **{p1['display_name']}** ↔ **{p2['display_name']}** getauscht.\n\n"
                + self._current_teams_text()
            )
        else:
            p, slot = self._find_player(args[0])
            if not p:
                return await self.send(f"❌ **{args[0]}** nicht in den aktuellen Teams.")

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
            )

    async def _match_setgk(self, args: List[str]):
        """!match gk Name → Spieler als Torwart seines Teams setzen."""
        if not self._has_teams():
            return await self.send("❌ Keine Teams vorhanden. Erst `!team` ausführen.")
        if not args:
            return await self.send("Syntax: `!match gk Name`")

        p, slot = self._find_player(args[0])
        if not p:
            return await self.send(f"❌ **{args[0]}** nicht in den aktuellen Teams.")

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
        )

    async def _match_switched(self, args: List[str]):
        """!match switched Name → Spieler von Wertung aus-/einschließen."""
        if not self._has_teams():
            return await self.send("❌ Keine Teams vorhanden. Erst `!team` ausführen.")
        if not args:
            return await self.send("Syntax: `!match switched Name`")

        p, slot = self._find_player(args[0])
        if not p:
            return await self.send(f"❌ **{args[0]}** nicht in den aktuellen Teams.")

        pid = p["id"]
        if pid in self._switched:
            self._switched.discard(pid)
            await self.send(f"↩️ **{p['display_name']}** – Wertung wieder aktiv.")
        else:
            self._switched.add(pid)
            await self.send(
                f"🔕 **{p['display_name']}** – keine Score-Wertung nach dem Spiel."
            )

    # ─────────────────────────────────────────────────────────────────────────
    # !result
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_result(self, args: List[str], sender: str):
        if not self._is_admin(sender):
            return await self.send("❌ Keine Berechtigung.")
        if not args:
            return await self.send("Syntax: `!result 3:2`")
        if not self._has_teams():
            return await self.send("❌ Keine Teams gespeichert. Erst `!team` ausführen.")

        try:
            s1, s2 = args[0].split(":")
            score1, score2 = int(s1), int(s2)
        except (ValueError, AttributeError):
            return await self.send("❌ Format: `!result 3:2`")

        all_t1 = self._t1_field + ([self._t1_gk] if self._t1_gk else [])
        all_t2 = self._t2_field + ([self._t2_gk] if self._t2_gk else [])
        ids1   = [p["id"] for p in all_t1]
        ids2   = [p["id"] for p in all_t2]
        gk1_id = self._t1_gk["id"] if self._t1_gk else None
        gk2_id = self._t2_gk["id"] if self._t2_gk else None

        await self.db.save_match(score1, score2, ids1, ids2, gk1_id, gk2_id)

        gk_ids    = [i for i in [gk1_id, gk2_id] if i]
        score_ids = [i for i in (ids1 + ids2) if i not in self._switched]
        await self.db.recalculate_scores(score_ids, gk_ids=gk_ids)

        # Ergebnis-Zeile
        if score1 > score2:
            result_line = "🏆 **Team 1 gewinnt!**"
        elif score2 > score1:
            result_line = "🏆 **Team 2 gewinnt!**"
        else:
            result_line = "🤝 **Unentschieden!**"

        def roster(field, gk):
            names = ([f"🧤{gk['display_name']}"] if gk else [])
            names += [p["display_name"] for p in field]
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

        await self.send(
            f"⚽ **Spielergebnis**\n\n"
            f"🔴 Team 1 **{score1}** – {roster(self._t1_field, self._t1_gk)}\n"
            f"🔵 Team 2 **{score2}** – {roster(self._t2_field, self._t2_gk)}\n\n"
            f"{result_line}{switched_note}\n\n"
            f"🔄 Scores aktualisiert."
        )

        self._reset_match_state()

    # ─────────────────────────────────────────────────────────────────────────
    # !team  /  !gk  /  !kein_gk
    # ─────────────────────────────────────────────────────────────────────────

    async def _cmd_team(self):
        vote = await self.db.get_open_vote()
        if not vote:
            return await self.send(
                "⚠️ Kein offener Vote vorhanden.\n"
                "Admin kann mit `!vote` einen neuen Vote starten."
            )

        yes_ids = await self.db.get_vote_yes_players(vote["id"])
        if not yes_ids:
            return await self.send("⚠️ Noch keine Zusagen im aktuellen Vote.")

        players, unknown = [], []
        for mid in yes_ids:
            p = await self.db.get_player(mid)
            if p and p["active"]:
                players.append(p)
            else:
                unknown.append(mid)

        if len(players) < 2:
            return await self.send(
                f"⚠️ Nur {len(players)} bekannte(r) Spieler mit Zusage – mindestens 2 nötig."
            )

        gk_requests = await self.db.get_gk_requests(vote["id"])

        t1f, gk1, t2f, gk2 = build_teams(players, gk_requests)

        self._t1_field = t1f
        self._t1_gk    = gk1
        self._t2_field = t2f
        self._t2_gk    = gk2
        self._switched = set()

        msg = format_teams(t1f, gk1, t2f, gk2)
        if unknown:
            msg += f"\n\n⚠️ Nicht in DB: {', '.join(unknown)}"
        await self.send(msg)

    async def _cmd_gk(self, sender: str, add: bool):
        vote = await self.db.get_open_vote()
        if not vote:
            return await self.send("⚠️ Kein offener Vote vorhanden.")

        yes_ids = await self.db.get_vote_yes_players(vote["id"])
        if sender not in yes_ids:
            return await self.send(
                "⚠️ Du hast noch keine Zusage gegeben. "
                "Erst ✅ im Vote klicken, dann `!gk` schreiben."
            )

        if add:
            await self.db.add_gk_request(vote["id"], sender)
            p = await self.db.get_player(sender)
            name = p["display_name"] if p else sender
            gk_list = await self.db.get_gk_requests(vote["id"])
            await self.send(
                f"🧤 **{name}** möchte Torwart spielen.\n"
                f"GK-Freiwillige bisher: {len(gk_list)}"
            )
        else:
            await self.db.remove_gk_request(vote["id"], sender)
            await self.send("👍 GK-Meldung zurückgezogen.")

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
            f"Kicken Morgen, {game_date_str} um "
            f"{cfg.game_hour:02d}:{cfg.game_minute:02d} Uhr"
        )

        poll_content = {
            "msgtype": "m.text",
            "body": f"📊 {title}\n✅ Dabei / ❌ Nicht dabei",
            "org.matrix.msc3381.poll.start": {
                "kind": "org.matrix.msc3381.poll.disclosed",
                "max_selections": 1,
                "question": {"body": f"⚽ {title}"},
                "answers": [
                    {"id": "yes", "org.matrix.msc3381.poll.answer.text": "✅ Dabei"},
                    {"id": "no",  "org.matrix.msc3381.poll.answer.text": "❌ Nicht dabei"},
                ],
            },
            "m.poll.start": {
                "kind": "m.poll.disclosed",
                "max_selections": 1,
                "question": {"body": f"⚽ {title}"},
                "answers": [
                    {"id": "yes", "m.text": "✅ Dabei"},
                    {"id": "no",  "m.text": "❌ Nicht dabei"},
                ],
            },
        }

        resp = await self.client.room_send(
            self.config.room_id, "org.matrix.msc3381.poll.start", poll_content
        )
        if isinstance(resp, RoomSendResponse):
            vote_date = game_date.strftime("%Y-%m-%d")
            vote_id   = await self.db.create_vote(resp.event_id, vote_date)
            logger.info("Poll gestartet – event_id=%s vote_id=%d", resp.event_id, vote_id)
            await self.send(
                f"🗳️ Vote gestartet!\n"
                f"Abstimmen mit ✅ / ❌ im Poll.\n"
                f"🧤 Als Torwart melden: `!gk` schreiben."
            )
        else:
            logger.error("Poll konnte nicht gepostet werden: %s", resp)

    async def _scheduled_teams(self):
        logger.info("Automatische Team-Generierung ausgelöst")
        await self._cmd_team()

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


def _build_sync_filter() -> dict:
    """
    Sync-Filter: nur Timeline-Events der letzten 7 Tage anfordern.
    Reduziert Last beim ersten Sync nach längerem Ausfall.
    """
    since_ms = int(
        (datetime.now().timestamp() - MAX_EVENT_AGE_SECONDS) * 1000
    )
    return {
        "room": {
            "timeline": {
                "limit": 50,
                "not_senders": [],
            }
        }
    }


def _md_to_html(text: str) -> str:
    import re
    text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.*?)`",       r"<code>\1</code>",     text)
    text = text.replace("\n", "<br/>")
    return text
