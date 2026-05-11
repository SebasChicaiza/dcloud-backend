"""Thin wrapper over redis-py with project-specific keys and helpers."""
from __future__ import annotations

import json
import time
from typing import Any, Iterable

import redis


# ---- key builders ----
def k_leader_lock() -> str: return "leader:lock"
def k_leader_epoch() -> str: return "leader:epoch"
def k_node(node_id: str) -> str: return f"nodes:{node_id}"
def k_nodes_active() -> str: return "nodes:active"
def k_run_status(run_id: str) -> str: return f"runs:{run_id}:status"
def k_run_manifest(run_id: str) -> str: return f"runs:{run_id}:manifest"
def k_run_stats(run_id: str) -> str: return f"runs:{run_id}:stats"
def k_run_meta(run_id: str) -> str: return f"runs:{run_id}:meta"
def k_stream_jobs(run_id: str) -> str: return f"stream:jobs:{run_id}"
def k_stream_commands(run_id: str) -> str: return f"stream:commands:{run_id}"
def k_stream_events(run_id: str) -> str: return f"stream:events:{run_id}"
def k_chunk(run_id: str, chunk_id: str) -> str: return f"chunk:{run_id}:{chunk_id}"
def k_chunks_done(run_id: str) -> str: return f"runs:{run_id}:chunks:done"

JOBS_GROUP = "dna-workers"


class RedisState:
    """Connection + helpers. Holds a single connection pool."""

    def __init__(self, url: str):
        self.url = url
        self.r: redis.Redis = redis.from_url(url, decode_responses=True)

    def ping(self) -> bool:
        return bool(self.r.ping())

    # ---- node heartbeat ----
    def write_heartbeat(self, node_id: str, fields: dict[str, Any]) -> None:
        fields = {k: ("" if v is None else v) for k, v in fields.items()}
        pipe = self.r.pipeline()
        pipe.hset(k_node(node_id), mapping=fields)
        pipe.sadd(k_nodes_active(), node_id)
        pipe.execute()

    def get_node(self, node_id: str) -> dict[str, str]:
        return self.r.hgetall(k_node(node_id))

    def list_active_nodes(self) -> list[str]:
        return list(self.r.smembers(k_nodes_active()))

    def mark_node_dead(self, node_id: str) -> None:
        self.r.hset(k_node(node_id), "status", "DEAD")
        self.r.srem(k_nodes_active(), node_id)

    # ---- run state ----
    def set_run_status(self, run_id: str, status: str) -> None:
        self.r.set(k_run_status(run_id), status)

    def get_run_status(self, run_id: str) -> str | None:
        return self.r.get(k_run_status(run_id))

    def save_manifest(self, run_id: str, manifest_json: str) -> None:
        self.r.set(k_run_manifest(run_id), manifest_json)

    def load_manifest(self, run_id: str) -> str | None:
        return self.r.get(k_run_manifest(run_id))

    def set_meta(self, run_id: str, field: str, value: str) -> None:
        self.r.hset(k_run_meta(run_id), field, value)

    def get_meta(self, run_id: str, field: str) -> str | None:
        return self.r.hget(k_run_meta(run_id), field)

    def incr_stat(self, run_id: str, field: str, by: int = 1) -> int:
        return int(self.r.hincrby(k_run_stats(run_id), field, by))

    def get_stats(self, run_id: str) -> dict[str, str]:
        return self.r.hgetall(k_run_stats(run_id))

    # ---- chunk state ----
    def set_chunk(self, run_id: str, chunk_id: str, fields: dict[str, Any]) -> None:
        self.r.hset(k_chunk(run_id, chunk_id), mapping={k: str(v) for k, v in fields.items()})

    def get_chunk(self, run_id: str, chunk_id: str) -> dict[str, str]:
        return self.r.hgetall(k_chunk(run_id, chunk_id))

    def mark_chunk_done(self, run_id: str, chunk_id: str) -> None:
        self.r.sadd(k_chunks_done(run_id), chunk_id)

    def chunks_done_count(self, run_id: str) -> int:
        return int(self.r.scard(k_chunks_done(run_id)))

    def all_chunks_done(self, run_id: str, total: int) -> bool:
        return self.chunks_done_count(run_id) >= total

    # ---- jobs stream ----
    def ensure_jobs_group(self, run_id: str) -> None:
        try:
            self.r.xgroup_create(k_stream_jobs(run_id), JOBS_GROUP, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def publish_job(self, run_id: str, chunk: dict[str, Any]) -> str:
        return self.r.xadd(k_stream_jobs(run_id), {"chunk": json.dumps(chunk)})

    def read_jobs(self, run_id: str, consumer: str, count: int, block_ms: int) -> list[tuple[str, list[tuple[str, dict]]]]:
        return self.r.xreadgroup(
            JOBS_GROUP, consumer,
            streams={k_stream_jobs(run_id): ">"},
            count=count, block=block_ms,
        ) or []

    def ack_job(self, run_id: str, msg_id: str) -> None:
        self.r.xack(k_stream_jobs(run_id), JOBS_GROUP, msg_id)

    def autoclaim_stale(self, run_id: str, consumer: str, min_idle_ms: int, count: int = 50) -> list[tuple[str, dict]]:
        """Reclaim stale jobs. Returns list of (msg_id, fields)."""
        try:
            res = self.r.xautoclaim(
                k_stream_jobs(run_id), JOBS_GROUP, consumer,
                min_idle_time=min_idle_ms, start_id="0-0", count=count,
            )
            # redis-py returns (next_id, claimed_messages, deleted_ids)
            if isinstance(res, (list, tuple)) and len(res) >= 2:
                return res[1] or []
        except redis.ResponseError:
            return []
        return []

    # ---- commands stream ----
    def publish_command(self, run_id: str, cmd: dict[str, Any]) -> str:
        return self.r.xadd(k_stream_commands(run_id), {"cmd": json.dumps(cmd)})

    def read_commands(self, run_id: str, last_id: str, block_ms: int = 1000) -> list[tuple[str, dict]]:
        res = self.r.xread({k_stream_commands(run_id): last_id}, block=block_ms, count=20)
        if not res:
            return []
        return res[0][1]

    # ---- events ----
    def publish_event(self, run_id: str, event: dict[str, Any]) -> None:
        self.r.xadd(k_stream_events(run_id), {"event": json.dumps(event)},
                    maxlen=10_000, approximate=True)
