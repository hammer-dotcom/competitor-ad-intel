"""SQLite state. The whole point of state: knowing what is NEW this week."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.config import SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS ads (
    key            TEXT PRIMARY KEY,      -- platform:ad_id
    platform       TEXT NOT NULL,
    ad_id          TEXT NOT NULL,
    competitor     TEXT NOT NULL,
    advertiser     TEXT,
    headline       TEXT,
    body           TEXT,
    cta_text       TEXT,
    landing_url    TEXT,
    permalink      TEXT,
    media_json     TEXT,
    first_seen     TEXT,
    last_seen      TEXT,
    is_active      INTEGER DEFAULT 1,
    first_ingested TEXT NOT NULL,
    last_ingested  TEXT NOT NULL,
    classification TEXT,                  -- JSON from the LLM
    classified_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_ads_competitor ON ads(competitor);
CREATE INDEX IF NOT EXISTS idx_ads_ingested   ON ads(first_ingested);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    new_ads    INTEGER,
    total_ads  INTEGER,
    notes      TEXT
);
"""


def connect() -> sqlite3.Connection:
    Path(SETTINGS.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SETTINGS.db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert(conn: sqlite3.Connection, ad: dict, competitor: str) -> bool:
    """Insert or refresh one ad. Returns True if it is new to us."""
    key = f"{ad['platform']}:{ad['ad_id']}"
    if not ad["ad_id"]:
        return False

    now = _now()
    row = conn.execute("SELECT key FROM ads WHERE key = ?", (key,)).fetchone()

    if row:
        conn.execute(
            """UPDATE ads SET last_seen = ?, is_active = ?, last_ingested = ?
               WHERE key = ?""",
            (ad.get("last_seen"), int(ad.get("is_active", True)), now, key),
        )
        return False

    conn.execute(
        """INSERT INTO ads (key, platform, ad_id, competitor, advertiser, headline,
                            body, cta_text, landing_url, permalink, media_json,
                            first_seen, last_seen, is_active, first_ingested, last_ingested)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            key, ad["platform"], ad["ad_id"], competitor, ad.get("advertiser"),
            ad.get("headline"), ad.get("body"), ad.get("cta_text"),
            ad.get("landing_url"), ad.get("permalink"),
            json.dumps(ad.get("media", [])),
            ad.get("first_seen"), ad.get("last_seen"),
            int(ad.get("is_active", True)), now, now,
        ),
    )
    return True


def unclassified(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ads WHERE classification IS NULL ORDER BY first_ingested DESC"
    ).fetchall()


def save_classification(conn: sqlite3.Connection, key: str, payload: dict) -> None:
    conn.execute(
        "UPDATE ads SET classification = ?, classified_at = ? WHERE key = ?",
        (json.dumps(payload), _now(), key),
    )


def new_since(conn: sqlite3.Connection, iso_cutoff: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ads WHERE first_ingested >= ? ORDER BY competitor, first_ingested DESC",
        (iso_cutoff,),
    ).fetchall()


def all_active(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM ads WHERE is_active = 1").fetchall()


def log_run(conn: sqlite3.Connection, new_ads: int, total: int, notes: str = "") -> None:
    conn.execute(
        "INSERT INTO runs (started_at, new_ads, total_ads, notes) VALUES (?,?,?,?)",
        (_now(), new_ads, total, notes),
    )
