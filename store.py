"""Durable persistence for AWS Landing Zone Studio (scenarios, snapshots, comments).

Backend is pluggable behind one API, chosen at runtime:

  * **PostgreSQL** when a connection URL is configured (``LZ_DATABASE_URL`` env, or
    the app copies it from ``st.secrets["DATABASE_URL"]``). This is what you want
    on **Streamlit Community Cloud**, whose container filesystem is ephemeral
    (wiped on redeploy/sleep) — a local SQLite file there is NOT durable for
    app-side writes. A free managed Postgres (Neon, Supabase, …) makes scenarios,
    drift snapshots, and comments genuinely durable *and* shared with the
    scheduled drift collector.
  * **SQLite** otherwise — perfect for local dev / single-host deploys.
    Location: ``LZ_DB_PATH`` env, else ``<this directory>/lz_scenarios.db``.

The module stays Streamlit-agnostic (reads env only) so it's unit-testable and
reusable by the headless collector. ``ON CONFLICT`` upsert syntax is shared by
both engines; only the parameter placeholder differs (``?`` vs ``%s``).
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

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS scenarios (
        username   TEXT NOT NULL,
        name       TEXT NOT NULL,
        data_json  TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (username, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        username    TEXT NOT NULL,
        ts          TEXT NOT NULL,
        label       TEXT NOT NULL,
        kind        TEXT NOT NULL,
        waf_overall INTEGER NOT NULL,
        scores_json TEXT NOT NULL,
        data_json   TEXT NOT NULL,
        PRIMARY KEY (username, ts)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS comments (
        scenario_key TEXT NOT NULL,
        ts           TEXT NOT NULL,
        author       TEXT NOT NULL,
        text         TEXT NOT NULL
    )
    """,
]


def _url() -> str:
    return (os.environ.get("LZ_DATABASE_URL") or "").strip()


def _is_pg() -> bool:
    return _url().startswith(("postgres://", "postgresql://"))


def _ph() -> str:
    """Parameter placeholder for the active backend."""
    return "%s" if _is_pg() else "?"


def _db_path() -> str:
    return os.environ.get("LZ_DB_PATH") or str(_DEFAULT_PATH)


def _connect():
    """Open a connection to the active backend and ensure the schema exists."""
    if _is_pg():
        import psycopg
        conn = psycopg.connect(_url())
        with conn.cursor() as cur:
            for ddl in _DDL:
                cur.execute(ddl)
        conn.commit()
        return conn
    conn = sqlite3.connect(_db_path(), check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    for ddl in _DDL:
        conn.execute(ddl)
    return conn


def _execute(query: str, params=(), fetch: bool = False):
    """Run a query against the active backend. Returns rows when ``fetch``."""
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall() if fetch else None
            conn.commit()
            return rows
        finally:
            conn.close()


def backend() -> str:
    return "postgresql" if _is_pg() else "sqlite"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now_precise() -> str:
    # microsecond precision — used where the timestamp is a primary key, so
    # rapid successive saves don't collide.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def available() -> bool:
    """True if the active backend can be opened (else the app falls back to session)."""
    try:
        _connect().close()
        return True
    except Exception:
        return False


def save_scenario(user: str, name: str, design_dict: dict) -> None:
    p = _ph()
    _execute(
        f"INSERT INTO scenarios (username, name, data_json, updated_at) "
        f"VALUES ({p}, {p}, {p}, {p}) "
        f"ON CONFLICT (username, name) DO UPDATE SET "
        f"data_json=EXCLUDED.data_json, updated_at=EXCLUDED.updated_at",
        (user, name, json.dumps(design_dict), _now()))


def load_scenarios(user: str) -> dict:
    """Return {name: design_dict} for the user (newest first)."""
    p = _ph()
    rows = _execute(
        f"SELECT name, data_json FROM scenarios WHERE username = {p} ORDER BY updated_at DESC",
        (user,), fetch=True) or []
    out = {}
    for name, data_json in rows:
        try:
            out[name] = json.loads(data_json)
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def delete_scenario(user: str, name: str) -> None:
    p = _ph()
    _execute(f"DELETE FROM scenarios WHERE username = {p} AND name = {p}", (user, name))


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
    p = _ph()
    _execute(
        f"INSERT INTO snapshots "
        f"(username, ts, label, kind, waf_overall, scores_json, data_json) "
        f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}) "
        f"ON CONFLICT (username, ts) DO UPDATE SET "
        f"label=EXCLUDED.label, kind=EXCLUDED.kind, waf_overall=EXCLUDED.waf_overall, "
        f"scores_json=EXCLUDED.scores_json, data_json=EXCLUDED.data_json",
        (user, _now_precise(), label, kind, int(waf_overall),
         json.dumps(scores), json.dumps(design_dict)))


def load_snapshots(user: str) -> list:
    """Return [{ts,label,kind,waf_overall,scores,design}] oldest-first."""
    p = _ph()
    rows = _execute(
        f"SELECT ts, label, kind, waf_overall, scores_json, data_json "
        f"FROM snapshots WHERE username = {p} ORDER BY ts ASC", (user,), fetch=True) or []
    out = []
    for ts, label, kind, waf_overall, scores_json, data_json in rows:
        try:
            out.append({"ts": str(ts), "label": label, "kind": kind,
                        "waf_overall": int(waf_overall),
                        "scores": json.loads(scores_json), "design": json.loads(data_json)})
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def delete_snapshot(user: str, ts: str) -> None:
    p = _ph()
    _execute(f"DELETE FROM snapshots WHERE username = {p} AND ts = {p}", (user, ts))


# ---------------------------------------------------------------------------
# Comments — lightweight collaboration threads keyed by scenario name
# ---------------------------------------------------------------------------

def add_comment(scenario_key: str, author: str, text: str) -> None:
    p = _ph()
    _execute(
        f"INSERT INTO comments (scenario_key, ts, author, text) VALUES ({p}, {p}, {p}, {p})",
        (scenario_key, _now(), author, text))


def load_comments(scenario_key: str) -> list:
    p = _ph()
    rows = _execute(
        f"SELECT ts, author, text FROM comments WHERE scenario_key = {p} ORDER BY ts ASC",
        (scenario_key,), fetch=True) or []
    return [{"ts": str(ts), "author": author, "text": text} for ts, author, text in rows]
