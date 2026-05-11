"""Tests for leader election — only one acquires; renewal needs matching token.

These tests require a running Redis. If Redis isn't reachable, they are skipped.
"""
from __future__ import annotations

import os
import time

import pytest
import redis as redis_lib

from dna_node.config import Config
from dna_node.leader_election import LeaderElection
from dna_node.redis_state import RedisState, k_leader_lock


REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture()
def redis_state():
    try:
        r = redis_lib.from_url(REDIS_URL, decode_responses=True)
        r.ping()
    except Exception:
        pytest.skip("Redis not reachable for tests")
    r.flushdb()
    yield RedisState(REDIS_URL)
    r.flushdb()


def _mk_config(node_id: str, priority: int) -> Config:
    os.environ["NODE_ID"] = node_id
    os.environ["NODE_PRIORITY"] = str(priority)
    os.environ["CAN_BE_LEADER"] = "true"
    os.environ["REDIS_URL"] = REDIS_URL
    os.environ["LEADER_LOCK_TTL_MS"] = "2000"
    os.environ["LEADER_RENEW_INTERVAL_SECONDS"] = "0.5"
    os.environ["RUN_ID"] = "test-run"
    return Config.from_env()


def test_only_one_leader(redis_state):
    a = LeaderElection(_mk_config("A", 100), redis_state)
    b = LeaderElection(_mk_config("B", 50), redis_state)
    a.start(); b.start()
    time.sleep(1.5)
    leaders = [n for n, e in (("A", a), ("B", b)) if e.is_leader()]
    a.stop(); b.stop()
    assert len(leaders) == 1


def test_failover_after_lock_expiry(redis_state):
    # Acquire as A, then delete the lock manually (simulating death) — B should pick up.
    a = LeaderElection(_mk_config("A", 100), redis_state)
    a.start()
    time.sleep(1.0)
    assert a.is_leader()
    # Simulate hard kill: stop renewal and clear lock.
    a.stop()
    redis_state.r.delete(k_leader_lock())

    b = LeaderElection(_mk_config("B", 50), redis_state)
    b.start()
    time.sleep(1.0)
    assert b.is_leader()
    b.stop()


def test_renew_rejects_foreign_token(redis_state):
    a = LeaderElection(_mk_config("A", 100), redis_state)
    a.start()
    time.sleep(0.7)
    assert a.is_leader()

    # Foreign owner overwrites the lock (shouldn't happen in practice — Redis NX
    # prevents it — but we want to ensure A's renew script won't extend it).
    redis_state.r.set(k_leader_lock(),
                      '{"node_id":"X","priority":1,"epoch":1,"token":"foreign","acquired_at":0}',
                      px=5000)
    time.sleep(1.5)  # A's renewer should detect mismatch
    # A may still believe it's leader until its own renew fails — give it a moment.
    a.stop()
    val = redis_state.r.get(k_leader_lock())
    assert val is not None and "foreign" in val
