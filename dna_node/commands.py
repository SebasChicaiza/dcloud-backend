"""Command channel — leader reads stream:commands:{run_id} and applies actions."""
from __future__ import annotations

import json
import logging
from typing import Any

from .config import Config
from .models import ChunkStatus, WorkerStatus
from .redis_state import RedisState, k_node
from .logging_config import log_event

log = logging.getLogger(__name__)


# Commands are addressed to a node id (or "*" for all). Workers read their own
# nodes:{id} hash for control flags (paused/draining/disabled).
def _set_node_flag(state: RedisState, node_id: str, status: str) -> None:
    state.r.hset(k_node(node_id), "control_status", status)


def apply_command(cfg: Config, state: RedisState, cmd: dict[str, Any]) -> None:
    op = cmd.get("op")
    target = cmd.get("node_id", "*")
    log_event(log, logging.INFO, "command.recv", f"op={op} target={target}")

    if op in ("PAUSE_WORKER", "RESUME_WORKER", "DRAIN_WORKER", "DISABLE_WORKER"):
        mapping = {
            "PAUSE_WORKER": WorkerStatus.PAUSED.value,
            "RESUME_WORKER": WorkerStatus.ACTIVE.value,
            "DRAIN_WORKER": WorkerStatus.DRAINING.value,
            "DISABLE_WORKER": WorkerStatus.DISABLED.value,
        }
        new_status = mapping[op]
        if target == "*":
            for n in state.list_active_nodes():
                _set_node_flag(state, n, new_status)
        else:
            _set_node_flag(state, target, new_status)
        return

    if op == "RETRY_CHUNK":
        chunk_id = cmd.get("chunk_id")
        if not chunk_id:
            return
        info = state.get_chunk(cfg.run_id, chunk_id)
        if not info:
            log_event(log, logging.WARNING, "command.retry.unknown", chunk_id)
            return
        state.set_chunk(cfg.run_id, chunk_id, {"status": ChunkStatus.RETRY.value})
        state.r.srem(f"runs:{cfg.run_id}:chunks:done", chunk_id)
        state.publish_job(cfg.run_id, {
            "chunk_id": chunk_id,
            "chunk_index": int(info.get("chunk_index", 0)),
            "start": int(info.get("start", 0)),
            "end": int(info.get("end", 0)),
            "retry": True,
        })
        log_event(log, logging.INFO, "command.retry.republished", chunk_id)
        return

    if op == "REBUILD_FINAL":
        state.set_meta(cfg.run_id, "rebuild_final", "1")
        return

    if op == "CANCEL_RUN":
        state.set_run_status(cfg.run_id, "CANCELLED")
        return

    log_event(log, logging.WARNING, "command.unknown", f"op={op}")
