"""Periodic node heartbeat writer.

Static node info (hostname, OS, CPU model, total memory, python version, ...)
is published once at startup to `nodes:{id}:info` so the frontend can show it
without us re-sending it every heartbeat. Heartbeats themselves are written as
STRING with TTL to nodes:{id}, with dynamic state (role/status/metrics/timestamp).
"""
from __future__ import annotations

import json
import logging
import os
import platform
import psutil
import socket
import sys
import threading
import time
from typing import Callable

from .config import Config
from .redis_state import RedisState, k_node

log = logging.getLogger(__name__)


def _read_cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _read_total_memory_bytes() -> int:
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb * 1024
    except OSError:
        pass
    return 0


def collect_static_info(cfg: Config) -> dict:
    """One-time node profile for the frontend."""
    return {
        "node_id": cfg.node_id,
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "platform": platform.platform(),
        "kernel": platform.version(),
        "machine": platform.machine(),
        "python_version": sys.version.split()[0],
        "cpu_count_logical": os.cpu_count() or 1,
        "cpu_model": _read_cpu_model(),
        "total_memory_bytes": _read_total_memory_bytes(),
        "max_concurrency": cfg.max_concurrency,
        "concurrency": cfg.worker_concurrency,
        "can_be_leader": cfg.can_be_leader,
        "leader_can_process": cfg.leader_can_process,
        "priority": cfg.node_priority,
        "registered_at": time.time(),
    }


class Heartbeat:
    def __init__(
        self,
        cfg: Config,
        state: RedisState,
        role_provider: Callable[[], str],
        status_provider: Callable[[], str],
        metrics_provider: Callable[[], dict],
    ):
        self.cfg = cfg
        self.state = state
        self._role = role_provider
        self._status = status_provider
        self._metrics = metrics_provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # Publish static info once. Use a separate hash so we don't have to
        # resend it every heartbeat tick.
        info = collect_static_info(self.cfg)
        self.state.r.hset(f"{k_node(self.cfg.node_id)}:info",
                          mapping={k: str(v) for k, v in info.items()})
        self._thread = threading.Thread(target=self._loop, name="heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                metrics = self._metrics() or {}
                # Get CPU and memory usage
                cpu_usage = psutil.cpu_percent(interval=0.1)
                mem_info = psutil.virtual_memory()
                memory_usage = mem_info.percent
                
                # Payload in exact format expected by dashboard
                payload = {
                    "nodeId": self.cfg.node_id,
                    "status": self._status(),
                    "priority": self.cfg.node_priority,
                    "canBeLeader": self.cfg.can_be_leader,
                    "cpuUsage": int(cpu_usage),
                    "memoryUsage": int(memory_usage),
                    "concurrency": self.cfg.worker_concurrency,
                    "activeJobs": metrics.get("current_jobs", 0),
                    "completedJobs": metrics.get("completed_jobs", 0),
                    "failedJobs": metrics.get("failed_jobs", 0),
                    "provider": self.cfg.provider,
                    "timestamp": int(time.time() * 1000),  # milliseconds Unix
                }
                self.state.write_heartbeat(self.cfg.node_id, payload)
            except Exception:
                log.exception("Heartbeat write failed")
            self._stop.wait(self.cfg.heartbeat_interval_seconds)
