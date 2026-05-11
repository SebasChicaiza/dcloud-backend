"""Entrypoint: starts heartbeat, leader-election, leader loop (if elected), and worker."""
from __future__ import annotations

import logging
import signal
import sys
import threading
import time

from .config import Config
from .heartbeat import Heartbeat
from .leader import Leader
from .leader_election import LeaderElection
from .logging_config import log_event, setup_logging
from .models import WorkerStatus
from .redis_state import RedisState, k_node
from .scp_client import ScpClient
from .worker import Worker, WorkerMetrics

log = logging.getLogger("dna_node")


def main() -> int:
    cfg = Config.from_env()
    setup_logging(cfg.node_id, cfg.run_id)

    log_event(log, logging.INFO, "node.boot",
              f"node={cfg.node_id} priority={cfg.node_priority} "
              f"concurrency={cfg.worker_concurrency} can_be_leader={cfg.can_be_leader}")

    state = RedisState(cfg.redis_url)
    # Wait for Redis to be reachable.
    for attempt in range(30):
        try:
            state.ping()
            break
        except Exception as e:
            log.warning("Redis not ready (%s), retrying...", e)
            time.sleep(1)
    else:
        log.error("Redis unreachable")
        return 1

    scp = ScpClient(cfg)
    metrics = WorkerMetrics()

    election = LeaderElection(cfg, state)
    leader_thread_holder: dict = {"leader": None}

    def role_provider() -> str:
        if election.is_leader():
            return "leader"
        return "worker"

    def status_provider() -> str:
        cur = state.r.hget(k_node(cfg.node_id), "control_status") or WorkerStatus.ACTIVE.value
        return cur

    hb = Heartbeat(cfg, state, role_provider, status_provider, metrics.snapshot)
    hb.start()
    election.start()

    # If this node can act as worker (i.e., not leader-only, or LEADER_CAN_PROCESS=true),
    # start the worker loop. We start it unconditionally and let leader-only nodes set
    # control_status=DISABLED via env if desired.
    worker: Worker | None = None
    if cfg.leader_can_process or not cfg.can_be_leader or True:
        # The "or True" keeps simple semantics for the demo: every node runs a worker.
        # If you want strict leader-only nodes, set CAN_BE_LEADER=true LEADER_CAN_PROCESS=false
        # and send a DISABLE_WORKER command after election.
        worker = Worker(cfg, state, scp, metrics)
        worker.start()

    # Leader loop is spawned lazily once we become leader (single-shot per term).
    def watch_leader_role():
        spawned = False
        while not _stop.is_set():
            if election.is_leader() and not spawned:
                leader = Leader(cfg, state, scp, election.is_leader)
                leader.start()
                leader_thread_holder["leader"] = leader
                spawned = True
            elif not election.is_leader() and spawned:
                # Lost leadership — stop the leader thread; another node will take over.
                if leader_thread_holder["leader"]:
                    leader_thread_holder["leader"].stop()
                    leader_thread_holder["leader"] = None
                spawned = False
            time.sleep(0.5)

    _stop = threading.Event()
    watcher = threading.Thread(target=watch_leader_role, daemon=True, name="leader-watch")
    watcher.start()

    # Signal handling
    def _shutdown(signum, frame):
        log_event(log, logging.INFO, "node.shutdown", f"signal={signum}")
        _stop.set()
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        while not _stop.is_set():
            time.sleep(1)
    finally:
        if worker:
            worker.stop()
        if leader_thread_holder["leader"]:
            leader_thread_holder["leader"].stop()
        election.stop()
        hb.stop()
        log_event(log, logging.INFO, "node.exit", "Goodbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
