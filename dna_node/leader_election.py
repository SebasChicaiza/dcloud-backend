"""Leader election via Redis SET NX PX with token-based safe renewal."""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from typing import Callable

from .config import Config
from .models import LeaderInfo
from .redis_state import RedisState, k_leader_lock, k_leader_epoch
from .logging_config import log_event

log = logging.getLogger(__name__)

# Lua: only renew lock if token matches.
RENEW_SCRIPT = """
local cur = redis.call('GET', KEYS[1])
if not cur then return 0 end
local ok, data = pcall(cjson.decode, cur)
if not ok then return 0 end
if data.token == ARGV[1] then
  redis.call('PEXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""

# Lua: release only if token matches.
RELEASE_SCRIPT = """
local cur = redis.call('GET', KEYS[1])
if not cur then return 0 end
local ok, data = pcall(cjson.decode, cur)
if not ok then return 0 end
if data.token == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""


class LeaderElection:
    def __init__(
        self,
        cfg: Config,
        state: RedisState,
        on_become_leader: Callable[[LeaderInfo], None] | None = None,
        on_lose_leader: Callable[[], None] | None = None,
    ):
        self.cfg = cfg
        self.state = state
        self._on_become_leader = on_become_leader
        self._on_lose_leader = on_lose_leader

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._is_leader = False
        self._token: str | None = None
        self._epoch: int = 0
        self._renew_sha: str | None = None
        self._release_sha: str | None = None
        self._lock = threading.Lock()

    # ---- public ----
    def start(self) -> None:
        if not self.cfg.can_be_leader:
            log_event(log, logging.INFO, "leader.disabled", "Node has CAN_BE_LEADER=false")
            return
        self._renew_sha = self.state.r.script_load(RENEW_SCRIPT)
        self._release_sha = self.state.r.script_load(RELEASE_SCRIPT)
        self._thread = threading.Thread(target=self._loop, name="leader-election", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._is_leader:
            self._release()
        if self._thread:
            self._thread.join(timeout=5)

    def is_leader(self) -> bool:
        with self._lock:
            return self._is_leader

    # ---- internal ----
    def _try_acquire(self) -> bool:
        token = secrets.token_hex(16)
        # Bump epoch atomically (best-effort; safe even if multiple bump it before acquiring)
        epoch = int(self.state.r.incr(k_leader_epoch()))
        info = LeaderInfo(
            node_id=self.cfg.node_id,
            priority=self.cfg.node_priority,
            epoch=epoch,
            token=token,
            acquired_at=time.time(),
        )
        ok = self.state.r.set(
            k_leader_lock(),
            info.to_json(),
            nx=True, px=self.cfg.leader_lock_ttl_ms,
        )
        if not ok:
            return False
        with self._lock:
            self._is_leader = True
            self._token = token
            self._epoch = epoch
        log_event(log, logging.INFO, "leader.acquired",
                  f"Became leader epoch={epoch}", role="leader")
        if self._on_become_leader:
            try:
                self._on_become_leader(info)
            except Exception:
                log.exception("on_become_leader callback failed")
        return True

    def _renew(self) -> bool:
        if not self._token or not self._renew_sha:
            return False
        try:
            res = self.state.r.evalsha(
                self._renew_sha, 1, k_leader_lock(),
                self._token, str(self.cfg.leader_lock_ttl_ms),
            )
            return int(res) == 1
        except Exception:
            log.exception("Lock renewal failed")
            return False

    def _release(self) -> None:
        if self._token and self._release_sha:
            try:
                self.state.r.evalsha(self._release_sha, 1, k_leader_lock(), self._token)
            except Exception:
                pass
        with self._lock:
            self._is_leader = False
            self._token = None

    def _step_down(self, reason: str) -> None:
        log_event(log, logging.WARNING, "leader.lost", f"Lost leadership: {reason}", role="leader")
        with self._lock:
            self._is_leader = False
            self._token = None
        if self._on_lose_leader:
            try:
                self._on_lose_leader()
            except Exception:
                log.exception("on_lose_leader callback failed")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                # ✅ FIX: Read _is_leader under lock to prevent race condition
                with self._lock:
                    is_leader = self._is_leader
                    token = self._token
                
                if is_leader:
                    if not self._renew():
                        self._step_down("renew failed (lock taken or expired)")
                else:
                    # Don't preempt a live leader; only try to acquire if lock is free.
                    if not self.state.r.exists(k_leader_lock()):
                        self._try_acquire()
            except Exception:
                log.exception("Leader loop error")
            self._stop.wait(self.cfg.leader_renew_interval_seconds)
