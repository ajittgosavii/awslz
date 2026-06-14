"""Backend-agnostic tests for store.py.

The same suite runs against **both** backends in CI: once with SQLite (default)
and once with a real PostgreSQL service container (LZ_DATABASE_URL set). This is
what keeps the Postgres code path (placeholders, ON CONFLICT upserts, schema)
covered on every push.
"""

import time

import store
import waf
from lz_core import LZDesign, score_design

USER = "ci-test"


def _design(strategy="Account per workload"):
    return LZDesign(account_strategy=strategy)


def test_backend_available():
    assert store.available() is True
    assert store.backend() in ("sqlite", "postgresql")


def test_scenarios_crud_and_upsert():
    store.save_scenario(USER, "s1", _design().to_dict())
    loaded = store.load_scenarios(USER)
    assert loaded["s1"]["account_strategy"] == "Account per workload"

    # upsert must replace, not duplicate
    store.save_scenario(USER, "s1", _design("Single account").to_dict())
    loaded = store.load_scenarios(USER)
    assert loaded["s1"]["account_strategy"] == "Single account"
    assert list(loaded).count("s1") == 1

    store.delete_scenario(USER, "s1")
    assert "s1" not in store.load_scenarios(USER)


def test_import_many():
    n = store.import_many(USER, {
        "a": _design("Account per workload").to_dict(),
        "b": _design("Account per environment").to_dict(),
        "bad": "not-a-dict",  # ignored
    })
    assert n == 2
    loaded = store.load_scenarios(USER)
    assert {"a", "b"} <= set(loaded)
    store.delete_scenario(USER, "a")
    store.delete_scenario(USER, "b")


def test_snapshots_history_unique_ts_and_ordering():
    d = _design()
    sc = score_design(d)
    ov = waf.overall_score(waf.assess(d))
    for lbl, kind in (("t1", "target"), ("a1", "actual"), ("a2", "actual")):
        store.save_snapshot(USER, lbl, kind, d.to_dict(), ov, sc)
        time.sleep(0.002)  # guarantee distinct microsecond timestamps (PK)

    snaps = store.load_snapshots(USER)
    labels = [s["label"] for s in snaps]
    assert labels.count("t1") == 1
    assert labels.count("a1") == 1
    assert labels.count("a2") == 1
    # oldest-first ordering
    assert [s["ts"] for s in snaps] == sorted(s["ts"] for s in snaps)
    # round-tripped types
    assert all(isinstance(s["waf_overall"], int) for s in snaps)
    assert all(isinstance(s["scores"], dict) and isinstance(s["design"], dict) for s in snaps)

    for s in snaps:
        store.delete_snapshot(USER, s["ts"])
    assert store.load_snapshots(USER) == []


def test_snapshot_target_vs_actual_roundtrip():
    d = _design("Account per workload per environment")
    sc = score_design(d)
    ov = waf.overall_score(waf.assess(d))
    store.save_snapshot(USER, "base", "target", d.to_dict(), ov, sc)
    snaps = store.load_snapshots(USER)
    target = [s for s in snaps if s["kind"] == "target"][-1]
    assert target["scores"] == sc
    assert target["design"]["account_strategy"] == "Account per workload per environment"
    store.delete_snapshot(USER, target["ts"])


def test_comments_thread_ordering():
    key = "ci-scenario-thread"
    store.add_comment(key, "alice", "first")
    time.sleep(0.002)
    store.add_comment(key, "bob", "second")
    cms = store.load_comments(key)
    texts = [c["text"] for c in cms]
    assert "first" in texts and "second" in texts
    # oldest-first ordering
    assert [c["ts"] for c in cms] == sorted(c["ts"] for c in cms)
