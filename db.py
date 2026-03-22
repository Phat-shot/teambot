"""
Database layer – SQLite via aiosqlite.

Tables:
  players            – score_field + score_gk + can_gk
  matches            – Matchergebnisse inkl. GK-IDs
  match_participations – Score je Spieler je Match (played_gk flag)
  votes              – Wöchentliche Abstimmungsnachrichten
  vote_responses     – Reaktionen der Spieler
  gk_requests        – !gk Anfragen pro Vote
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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    matrix_id    TEXT    UNIQUE NOT NULL,
    display_name TEXT    NOT NULL,
    score_field  REAL    NOT NULL DEFAULT 5.0,
    score_gk     REAL    NOT NULL DEFAULT 5.0,
    can_gk       INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
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
            "ALTER TABLE players ADD COLUMN score_field REAL NOT NULL DEFAULT 5.0",
            "ALTER TABLE players ADD COLUMN score_gk REAL NOT NULL DEFAULT 5.0",
            "ALTER TABLE players ADD COLUMN can_gk INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE matches ADD COLUMN team1_gk_id INTEGER",
            "ALTER TABLE matches ADD COLUMN team2_gk_id INTEGER",
            "ALTER TABLE match_participations ADD COLUMN played_gk INTEGER NOT NULL DEFAULT 0",
        ]
        for sql in migrations:
            try:
                await self._db.execute(sql)
                await self._db.commit()
            except Exception:
                pass  # already exists

    async def close(self):
        if self._db:
            await self._db.close()

    # ------------------------------------------------------------------
    # Players
    # ------------------------------------------------------------------

    async def add_player(self, matrix_id: str, display_name: str, can_gk: bool = False) -> int:
        async with self._db.execute(
            "INSERT INTO players (matrix_id, display_name, can_gk) VALUES (?,?,?)",
            (matrix_id, display_name, int(can_gk)),
        ) as cur:
            await self._db.commit()
            return cur.lastrowid  # type: ignore

    async def get_player(self, matrix_id: str) -> Optional[Dict]:
        async with self._db.execute(
            "SELECT * FROM players WHERE matrix_id = ?", (matrix_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def find_player(self, query: str) -> Optional[Dict]:
        """
        Flexible Spieler-Suche: akzeptiert Matrix-ID (@user:server)
        oder Anzeigename (case-insensitive, Teilstring reicht nicht –
        exakter Match bevorzugt, sonst None).
        """
        # Erst exakt per Matrix-ID
        if query.startswith("@"):
            async with self._db.execute(
                "SELECT * FROM players WHERE matrix_id = ? AND active = 1",
                (query,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return dict(row)
        # Dann exakt per Anzeigename (case-insensitive)
        async with self._db.execute(
            "SELECT * FROM players WHERE lower(display_name) = lower(?) AND active = 1",
            (query,)
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

    async def update_field_score(self, matrix_id: str, score: float):
        await self._db.execute(
            "UPDATE players SET score_field = ? WHERE matrix_id = ?",
            (round(min(10.0, max(0.0, score)), 2), matrix_id),
        )
        await self._db.commit()

    async def update_gk_score(self, matrix_id: str, score: float):
        await self._db.execute(
            "UPDATE players SET score_gk = ? WHERE matrix_id = ?",
            (round(min(10.0, max(0.0, score)), 2), matrix_id),
        )
        await self._db.commit()

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
        await self._db.execute("UPDATE votes SET closed = 1 WHERE closed = 0")
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
    ) -> int:
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

        goal_diff_t1 =  team1_score - team2_score
        goal_diff_t2 = -goal_diff_t1

        for pid in team1_ids:
            ms = _goal_diff_to_score(goal_diff_t1)
            await self._db.execute(
                """INSERT INTO match_participations
                   (match_id, player_id, team, played_gk, goal_diff, match_score)
                   VALUES (?,?,1,?,?,?)""",
                (match_id, pid, int(pid == team1_gk_id), goal_diff_t1, ms),
            )
        for pid in team2_ids:
            ms = _goal_diff_to_score(goal_diff_t2)
            await self._db.execute(
                """INSERT INTO match_participations
                   (match_id, player_id, team, played_gk, goal_diff, match_score)
                   VALUES (?,?,2,?,?,?)""",
                (match_id, pid, int(pid == team2_gk_id), goal_diff_t2, ms),
            )

        await self._db.commit()
        return match_id  # type: ignore

    async def recalculate_scores(
        self,
        player_ids: List[int],
        gk_ids: Optional[List[int]] = None,
    ):
        """
        Score-Neuberechnung:
          50 % Basis      (all-time Durchschnitt aller Spiele)
          30 % Letzte 5   (Durchschnitt der letzten 5 Spiele)
          20 % Letztes    (letztes Spiel)

        field-Score: nur Spiele als Feldspieler
        gk-Score:    nur Spiele als Torwart (nur für gk_ids)
        """
        gk_set = set(gk_ids or [])

        for pid in player_ids:
            # ── Field score ──────────────────────────────────────────────
            base_field = await self._scalar(
                "SELECT AVG(match_score) FROM match_participations WHERE player_id=? AND played_gk=0",
                (pid,), default=None,
            )
            if base_field is not None:
                last5_field = await self._scalar(
                    """SELECT AVG(match_score) FROM (
                         SELECT mp.match_score FROM match_participations mp
                         JOIN matches m ON mp.match_id = m.id
                         WHERE mp.player_id=? AND mp.played_gk=0
                         ORDER BY m.played_at DESC LIMIT 5
                       )""",
                    (pid,), default=base_field,
                )
                last1_field = await self._scalar(
                    """SELECT mp.match_score FROM match_participations mp
                       JOIN matches m ON mp.match_id = m.id
                       WHERE mp.player_id=? AND mp.played_gk=0
                       ORDER BY m.played_at DESC LIMIT 1""",
                    (pid,), default=base_field,
                )
                new_field = round(
                    min(10.0, max(0.0,
                        base_field * 0.50 + last5_field * 0.30 + last1_field * 0.20
                    )), 2
                )
                await self._db.execute(
                    "UPDATE players SET score_field=? WHERE id=?", (new_field, pid)
                )

            # ── GK score (nur für Spieler die diesmal als GK gespielt haben) ──
            if pid in gk_set:
                base_gk = await self._scalar(
                    "SELECT AVG(match_score) FROM match_participations WHERE player_id=? AND played_gk=1",
                    (pid,), default=None,
                )
                if base_gk is not None:
                    last5_gk = await self._scalar(
                        """SELECT AVG(match_score) FROM (
                             SELECT mp.match_score FROM match_participations mp
                             JOIN matches m ON mp.match_id = m.id
                             WHERE mp.player_id=? AND mp.played_gk=1
                             ORDER BY m.played_at DESC LIMIT 5
                           )""",
                        (pid,), default=base_gk,
                    )
                    last1_gk = await self._scalar(
                        """SELECT mp.match_score FROM match_participations mp
                           JOIN matches m ON mp.match_id = m.id
                           WHERE mp.player_id=? AND mp.played_gk=1
                           ORDER BY m.played_at DESC LIMIT 1""",
                        (pid,), default=base_gk,
                    )
                    new_gk = round(
                        min(10.0, max(0.0,
                            base_gk * 0.50 + last5_gk * 0.30 + last1_gk * 0.20
                        )), 2
                    )
                    await self._db.execute(
                        "UPDATE players SET score_gk=? WHERE id=?", (new_gk, pid)
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
