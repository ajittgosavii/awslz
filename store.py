"""Durable scenario persistence for AWS Landing Zone Studio.

Scenarios used to live only in ``st.session_state`` (lost on refresh / new
session). This module persists them to a SQLite database, keyed by user, so a
named design survives restarts and is available across sessions for the same
user — with JSON export/import retained for portability.

The module is intentionally Streamlit-agnostic (pure ``sqlite3``) so it can be
unit-tested and reused. The app passes the current user in.

Storage location precedence:
  1. ``LZ_DB_PATH`` environment variable, if set;
  2. ``<this directory>/lz_scenarios.db``.

NOTE on Streamlit Community Cloud: container storage there is ephemeral and is
wiped on redeploy/sleep. For durable multi-user storage on that platform, point
``LZ_DB_PATH`` at a mounted volume or swap this module's connection for a managed
database (the public API below stays the same).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_DEFAULT_PATH = Path(__file__).with_name("lz_scenarios.db")


def _db_path() -> str:
    return os.environ.get("LZ_DB_PATH") or str(_DEFAULT_PATH)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scenarios (
            username   TEXT NOT NULL,
            name       TEXT NOT NULL,
            data_json  TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (username, name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            username    TEXT NOT NULL,
            ts          TEXT NOT NULL,
            label       TEXT NOT NULL,
            kind        TEXT NOT NULL,           -- 'target' | 'actual'
            waf_overall INTEGER NOT NULL,
            scores_json TEXT NOT NULL,
            data_json   TEXT NOT NULL,
            PRIMARY KEY (username, ts)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            scenario_key TEXT NOT NULL,
            ts           TEXT NOT NULL,
            author       TEXT NOT NULL,
            text         TEXT NOT NULL
        )
        """
    )
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_precise() -> str:
    # microsecond precision — used where the timestamp is a primary key, so
    # rapid successive saves don't collide.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def available() -> bool:
    """True if the SQLite store can be opened (else the app falls back to session)."""
    try:
        with _LOCK:
            _connect().close()
        return True
    except Exception:
        return False


def save_scenario(user: str, name: str, design_dict: dict) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO scenarios (username, name, data_json, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(username, name) DO UPDATE SET "
                "data_json=excluded.data_json, updated_at=excluded.updated_at",
                (user, name, json.dumps(design_dict), _now()),
            )
            conn.commit()
        finally:
            conn.close()


def load_scenarios(user: str) -> dict:
    """Return {name: design_dict} for the user (newest first)."""
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT name, data_json FROM scenarios WHERE username = ? "
                "ORDER BY updated_at DESC",
                (user,),
            ).fetchall()
        finally:
            conn.close()
    out = {}
    for name, data_json in rows:
        try:
            out[name] = json.loads(data_json)
        except json.JSONDecodeError:
            continue
    return out


def delete_scenario(user: str, name: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM scenarios WHERE username = ? AND name = ?", (user, name))
            conn.commit()
        finally:
            conn.close()


def import_many(user: str, scenarios: dict) -> int:
    """Bulk-upsert {name: design_dict}. Returns the count written."""
    count = 0
    for name, ddict in scenarios.items():
        if isinstance(ddict, dict):
            save_scenario(user, name, ddict)
            count += 1
    return count


# ---------------------------------------------------------------------------
# Snapshots — point-in-time captures for drift / history tracking
# ---------------------------------------------------------------------------

def save_snapshot(user: str, label: str, kind: str, design_dict: dict,
                  waf_overall: int, scores: dict) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots "
                "(username, ts, label, kind, waf_overall, scores_json, data_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user, _now_precise(), label, kind, int(waf_overall),
                 json.dumps(scores), json.dumps(design_dict)),
            )
            conn.commit()
        finally:
            conn.close()


def load_snapshots(user: str) -> list:
    """Return [{ts,label,kind,waf_overall,scores,design}] oldest-first."""
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT ts, label, kind, waf_overall, scores_json, data_json "
                "FROM snapshots WHERE username = ? ORDER BY ts ASC", (user,)).fetchall()
        finally:
            conn.close()
    out = []
    for ts, label, kind, waf_overall, scores_json, data_json in rows:
        try:
            out.append({"ts": ts, "label": label, "kind": kind, "waf_overall": waf_overall,
                        "scores": json.loads(scores_json), "design": json.loads(data_json)})
        except json.JSONDecodeError:
            continue
    return out


def delete_snapshot(user: str, ts: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM snapshots WHERE username = ? AND ts = ?", (user, ts))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Comments — lightweight collaboration threads keyed by scenario name
# ---------------------------------------------------------------------------

def add_comment(scenario_key: str, author: str, text: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO comments (scenario_key, ts, author, text) VALUES (?, ?, ?, ?)",
                (scenario_key, _now(), author, text))
            conn.commit()
        finally:
            conn.close()


def load_comments(scenario_key: str) -> list:
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT ts, author, text FROM comments WHERE scenario_key = ? ORDER BY ts ASC",
                (scenario_key,)).fetchall()
        finally:
            conn.close()
    return [{"ts": ts, "author": author, "text": text} for ts, author, text in rows]
