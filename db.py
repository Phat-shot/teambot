"""
Database layer – SQLite via aiosqlite.

Tables:
  players            – score + can_gk flag
  matches            – Matchergebnisse inkl. GK-IDs
  match_participations – Score je Spieler je Match
  votes              – Wöchentliche Abstimmungsnachrichten
  vote_responses     – Reaktionen der Spieler
  gk_requests        – 🥅 Anfragen pro Vote
"""

import json
import logging
import os
from typing import Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    player_number TEXT    UNIQUE,           -- interne ID 0001-9999
    matrix_id     TEXT    UNIQUE NOT NULL,
    display_name  TEXT    NOT NULL,
    score         REAL    NOT NULL DEFAULT 5.0,
    score_base    REAL    NOT NULL DEFAULT 5.0,
    can_gk        INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS matches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    played_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    team1_score      INTEGER NOT NULL,
    team2_score      INTEGER NOT NULL,
    team1_player_ids TEXT    NOT NULL,
    team2_player_ids TEXT    NOT NULL,
    team1_gk_id      INTEGER,
    team2_gk_id      INTEGER
);

CREATE TABLE IF NOT EXISTS match_participations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id    INTEGER NOT NULL REFERENCES matches(id),
    player_id   INTEGER NOT NULL REFERENCES players(id),
    team        INTEGER NOT NULL,
    played_gk   INTEGER NOT NULL DEFAULT 0,
    goal_diff   INTEGER NOT NULL,
    match_score REAL    NOT NULL,
    UNIQUE(match_id, player_id)
);

CREATE TABLE IF NOT EXISTS votes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT    UNIQUE,
    vote_date   TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    closed      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vote_responses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vote_id      INTEGER NOT NULL REFERENCES votes(id),
    matrix_id    TEXT    NOT NULL,
    response     TEXT    NOT NULL,
    responded_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(vote_id, matrix_id)
);

CREATE TABLE IF NOT EXISTS gk_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vote_id      INTEGER NOT NULL REFERENCES votes(id),
    matrix_id    TEXT    NOT NULL,
    requested_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(vote_id, matrix_id)
);
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        dir_name = os.path.dirname(self.db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        await self._migrate()
        logger.info("Database ready: %s", self.db_path)

    async def _migrate(self):
        """Add new columns to existing DB if upgrading from old schema."""
        migrations = [
            "ALTER TABLE players ADD COLUMN score REAL NOT NULL DEFAULT 5.0",
            "ALTER TABLE players ADD COLUMN score_base REAL NOT NULL DEFAULT 5.0",
            "ALTER TABLE players ADD COLUMN can_gk INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE players ADD COLUMN player_number TEXT",
            "ALTER TABLE matches ADD COLUMN team1_gk_id INTEGER",
            "ALTER TABLE matches ADD COLUMN team2_gk_id INTEGER",
            "ALTER TABLE match_participations ADD COLUMN played_gk INTEGER NOT NULL DEFAULT 0",
            "UPDATE players SET score = score_field, score_base = score_base WHERE score_field IS NOT NULL AND score = 5.0",
        ]
        for sql in migrations:
            try:
                await self._db.execute(sql)
                await self._db.commit()
            except Exception:
                pass

        # Bestehende Spieler ohne player_number nachträglich befüllen
        async with self._db.execute(
            "SELECT id FROM players WHERE player_number IS NULL ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            num = await self._next_player_number()
            await self._db.execute(
                "UPDATE players SET player_number=? WHERE id=?", (num, row[0])
            )
        if rows:
            await self._db.commit()  # already exists or not applicable

    async def close(self):
        if self._db:
            await self._db.close()

    # ------------------------------------------------------------------
    # Players
    # ------------------------------------------------------------------

    async def _next_player_number(self) -> str:
        """Nächste freie interne Spielernummer 0001–9999."""
        async with self._db.execute(
            "SELECT player_number FROM players WHERE player_number IS NOT NULL ORDER BY player_number"
        ) as cur:
            rows = await cur.fetchall()
        used = {r[0] for r in rows}
        for i in range(1, 10000):
            num = f"{i:04d}"
            if num not in used:
                return num
        raise ValueError("Alle Spielernummern vergeben (0001–9999)")

    async def add_player(self, matrix_id: str, display_name: str, can_gk: bool = False) -> int:
        num = await self._next_player_number()
        async with self._db.execute(
            "INSERT INTO players (player_number, matrix_id, display_name, can_gk) VALUES (?,?,?,?)",
            (num, matrix_id, display_name, int(can_gk)),
        ) as cur:
            await self._db.commit()
            return cur.lastrowid  # type: ignore

    async def get_player_by_number(self, number: str) -> Optional[Dict]:
        """Spieler über interne ID finden (z.B. '0042')."""
        padded = number.zfill(4)
        async with self._db.execute(
            "SELECT * FROM players WHERE player_number = ?", (padded,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def rename_player(self, matrix_id: str, new_name: str):
        await self._db.execute(
            "UPDATE players SET display_name=? WHERE matrix_id=?", (new_name, matrix_id)
        )
        await self._db.commit()

    async def get_player(self, matrix_id: str) -> Optional[Dict]:
        async with self._db.execute(
            "SELECT * FROM players WHERE matrix_id = ?", (matrix_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def find_player(self, query: str) -> Optional[Dict]:
        if query.startswith("@"):
            async with self._db.execute(
                "SELECT * FROM players WHERE matrix_id = ? AND active = 1", (query,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return dict(row)
        async with self._db.execute(
            "SELECT * FROM players WHERE lower(display_name) = lower(?) AND active = 1", (query,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_player_by_id(self, player_id: int) -> Optional[Dict]:
        async with self._db.execute(
            "SELECT * FROM players WHERE id = ?", (player_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_all_players(self, active_only: bool = True) -> List[Dict]:
        sql = "SELECT * FROM players"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY display_name COLLATE NOCASE"
        async with self._db.execute(sql) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def update_score(self, matrix_id: str, score: float):
        """Manuell gesetzter Basis-Score (Admin). Wird nie durch Match-Ergebnisse überschrieben."""
        s = round(min(10.0, max(0.0, score)), 2)
        await self._db.execute(
            "UPDATE players SET score=?, score_base=? WHERE matrix_id=?",
            (s, s, matrix_id),
        )
        await self._db.commit()

    # Legacy aliases
    async def update_field_score(self, matrix_id: str, score: float):
        await self.update_score(matrix_id, score)

    async def set_can_gk(self, matrix_id: str, can_gk: bool):
        await self._db.execute(
            "UPDATE players SET can_gk = ? WHERE matrix_id = ?",
            (int(can_gk), matrix_id),
        )
        await self._db.commit()

    async def deactivate_player(self, matrix_id: str):
        await self._db.execute(
            "UPDATE players SET active = 0 WHERE matrix_id = ?", (matrix_id,)
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Votes
    # ------------------------------------------------------------------

    async def create_vote(self, event_id: str, vote_date: str) -> int:
        async with self._db.execute(
            "INSERT INTO votes (event_id, vote_date) VALUES (?,?)",
            (event_id, vote_date),
        ) as cur:
            await self._db.commit()
            return cur.lastrowid  # type: ignore

    async def get_open_vote(self) -> Optional[Dict]:
        async with self._db.execute(
            "SELECT * FROM votes WHERE closed = 0 ORDER BY created_at DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_vote_by_event(self, event_id: str) -> Optional[Dict]:
        async with self._db.execute(
            "SELECT * FROM votes WHERE event_id = ?", (event_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def upsert_vote_response(self, vote_id: int, matrix_id: str, response: str):
        await self._db.execute(
            """INSERT INTO vote_responses (vote_id, matrix_id, response)
               VALUES (?,?,?)
               ON CONFLICT(vote_id, matrix_id)
               DO UPDATE SET response = excluded.response,
                             responded_at = datetime('now')""",
            (vote_id, matrix_id, response),
        )
        await self._db.commit()

    async def get_vote_yes_players(self, vote_id: int) -> List[str]:
        async with self._db.execute(
            "SELECT matrix_id FROM vote_responses WHERE vote_id = ? AND response = 'yes'",
            (vote_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]

    async def close_vote(self, vote_id: int):
        await self._db.execute("UPDATE votes SET closed = 1 WHERE id = ?", (vote_id,))
        await self._db.commit()

    # ------------------------------------------------------------------
    # GK requests
    # ------------------------------------------------------------------

    async def add_gk_request(self, vote_id: int, matrix_id: str):
        await self._db.execute(
            "INSERT OR IGNORE INTO gk_requests (vote_id, matrix_id) VALUES (?,?)",
            (vote_id, matrix_id),
        )
        await self._db.commit()

    async def remove_gk_request(self, vote_id: int, matrix_id: str):
        await self._db.execute(
            "DELETE FROM gk_requests WHERE vote_id = ? AND matrix_id = ?",
            (vote_id, matrix_id),
        )
        await self._db.commit()

    async def get_gk_requests(self, vote_id: int) -> List[str]:
        async with self._db.execute(
            "SELECT matrix_id FROM gk_requests WHERE vote_id = ?", (vote_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # Matches & Score recalculation
    # ------------------------------------------------------------------

    async def save_match(
        self,
        team1_score: int,
        team2_score: int,
        team1_ids: List[int],
        team2_ids: List[int],
        team1_gk_id: Optional[int] = None,
        team2_gk_id: Optional[int] = None,
        team1_avg: float = 5.0,
        team2_avg: float = 5.0,
        K: float = 0.3,
    ) -> int:
        """
        Speichert ein Match. Match-Score per Elo-Erwartungskorrektur:
          match_score = clamp(basis + K × (ergebnis − erwartung) × 10)
          ergebnis: Sieg=1, Unentschieden=0.5, Niederlage=0
          erwartung: 1 / (1 + 10^((Ø_gegner − Ø_team) / 4))
        """
        async with self._db.execute(
            """INSERT INTO matches
               (team1_score, team2_score, team1_player_ids, team2_player_ids,
                team1_gk_id, team2_gk_id)
               VALUES (?,?,?,?,?,?)""",
            (team1_score, team2_score,
             json.dumps(team1_ids), json.dumps(team2_ids),
             team1_gk_id, team2_gk_id),
        ) as cur:
            match_id = cur.lastrowid

        won1  = team1_score > team2_score
        won2  = team2_score > team1_score
        draw  = team1_score == team2_score
        act1  = 1.0 if won1 else (0.5 if draw else 0.0)
        act2  = 1.0 if won2 else (0.5 if draw else 0.0)
        exp1  = 1.0 / (1.0 + 10 ** ((team2_avg - team1_avg) / 4.0))
        exp2  = 1.0 - exp1
        goal_diff_t1 =  team1_score - team2_score
        goal_diff_t2 = -goal_diff_t1

        for pid in team1_ids:
            base = await self._scalar(
                "SELECT score_base FROM players WHERE id=?", (pid,), default=5.0
            )
            ms = round(min(10.0, max(0.0, base + K * (act1 - exp1) * 10)), 3)
            await self._db.execute(
                """INSERT INTO match_participations
                   (match_id, player_id, team, played_gk, goal_diff, match_score)
                   VALUES (?,?,1,?,?,?)""",
                (match_id, pid, int(pid == team1_gk_id), goal_diff_t1, ms),
            )
        for pid in team2_ids:
            base = await self._scalar(
                "SELECT score_base FROM players WHERE id=?", (pid,), default=5.0
            )
            ms = round(min(10.0, max(0.0, base + K * (act2 - exp2) * 10)), 3)
            await self._db.execute(
                """INSERT INTO match_participations
                   (match_id, player_id, team, played_gk, goal_diff, match_score)
                   VALUES (?,?,2,?,?,?)""",
                (match_id, pid, int(pid == team2_gk_id), goal_diff_t2, ms),
            )

        await self._db.commit()
        return match_id  # type: ignore

    async def recalculate_scores(self, player_ids: List[int], **kwargs):
        """
        Score-Neuberechnung:
          score = 50% score_base (manuell, nie überschrieben)
                + 25% Ø gesamte History (ohne letzte 3 Spiele)
                + 25% Ø letzte 3 Spiele

        score_base wird NIE durch Match-Ergebnisse geändert –
        nur durch expliziten Admin-Befehl (!player set).
        """
        for pid in player_ids:
            async with self._db.execute(
                "SELECT score_base FROM players WHERE id=?", (pid,)
            ) as cur:
                row = await cur.fetchone()
                base = float(row[0]) if row else 5.0

            # Alle Match-Scores chronologisch
            async with self._db.execute(
                """SELECT mp.match_score FROM match_participations mp
                   JOIN matches m ON mp.match_id = m.id
                   WHERE mp.player_id = ?
                   ORDER BY m.played_at ASC""",
                (pid,)
            ) as cur:
                rows = await cur.fetchall()
            all_scores = [float(r[0]) for r in rows]

            if not all_scores:
                # Noch keine Spiele → Score bleibt Basis
                continue

            last3  = all_scores[-3:]
            rest   = all_scores[:-3] if len(all_scores) > 3 else []

            avg_last3 = sum(last3) / len(last3)
            avg_rest  = sum(rest) / len(rest) if rest else base

            new_score = round(
                min(10.0, max(0.0,
                    base * 0.50 + avg_rest * 0.25 + avg_last3 * 0.25
                )), 2
            )

            # score wird aktualisiert, score_base NICHT
            await self._db.execute(
                "UPDATE players SET score=? WHERE id=?",
                (new_score, pid),
            )

        await self._db.commit()
        logger.info("Scores neu berechnet für %d Spieler", len(player_ids))

    async def get_last_match(self) -> Optional[Dict]:
        async with self._db.execute(
            "SELECT * FROM matches ORDER BY played_at DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            d["team1_player_ids"] = json.loads(d["team1_player_ids"])
            d["team2_player_ids"] = json.loads(d["team2_player_ids"])
            return d

    async def get_last_matches(self, n: int = 5) -> List[Dict]:
        async with self._db.execute(
            "SELECT * FROM matches ORDER BY played_at DESC LIMIT ?", (n,)
        ) as cur:
            rows = await cur.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["team1_player_ids"] = json.loads(d["team1_player_ids"])
                d["team2_player_ids"] = json.loads(d["team2_player_ids"])
                result.append(d)
            return result

    async def _scalar(self, sql: str, params: tuple, default):
        async with self._db.execute(sql, params) as cur:
            row = await cur.fetchone()
            val = row[0] if row else None
            if val is None:
                return default
            return float(val)


def _goal_diff_to_score(goal_diff: int) -> float:
    return float(min(10, max(0, 5 + goal_diff)))
