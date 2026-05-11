"""Manifest creation: deterministic chunk plan from input file sizes."""
from __future__ import annotations

import logging

from .config import Config
from .models import ChunkSpec, ChunkStatus, Manifest
from .redis_state import RedisState
from .scp_client import ScpClient
from .logging_config import log_event

log = logging.getLogger(__name__)


def build_manifest(run_id: str, size_a: int, size_b: int, chunk_size: int) -> Manifest:
    warnings: list[str] = []
    if size_a != size_b:
        warnings.append(
            f"Input sizes differ: A={size_a} B={size_b}; using min size for comparable region."
        )
    comparable = min(size_a, size_b)
    chunks: list[ChunkSpec] = []
    idx = 0
    pos = 0
    while pos < comparable:
        end = min(pos + chunk_size, comparable)
        chunk_id = f"chunk_{idx:06d}"
        chunks.append(ChunkSpec(chunk_id=chunk_id, chunk_index=idx, start=pos, end=end))
        idx += 1
        pos = end
    return Manifest(
        run_id=run_id,
        input_a_size=size_a,
        input_b_size=size_b,
        comparable_size=comparable,
        chunk_size=chunk_size,
        chunks=chunks,
        warnings=warnings,
    )


def ensure_manifest_and_jobs(cfg: Config, state: RedisState, scp: ScpClient) -> Manifest:
    """Idempotent: build manifest from remote input sizes and publish jobs once."""
    existing = state.load_manifest(cfg.run_id)
    if existing:
        manifest = Manifest.from_json(existing)
        log_event(log, logging.INFO, "manifest.exists",
                  f"Reusing manifest chunks={len(manifest.chunks)}")
    else:
        remote_a = f"{cfg.remote_inputs_dir}/{cfg.input_a_name}"
        remote_b = f"{cfg.remote_inputs_dir}/{cfg.input_b_name}"
        size_a = scp.remote_size(remote_a)
        size_b = scp.remote_size(remote_b)
        manifest = build_manifest(cfg.run_id, size_a, size_b, cfg.chunk_size_bytes)
        state.save_manifest(cfg.run_id, manifest.to_json())
        state.set_meta(cfg.run_id, "total_chunks", str(len(manifest.chunks)))
        state.set_meta(cfg.run_id, "comparable_size", str(manifest.comparable_size))
        for w in manifest.warnings:
            log_event(log, logging.WARNING, "manifest.warning", w)
        log_event(log, logging.INFO, "manifest.created",
                  f"chunks={len(manifest.chunks)} comparable={manifest.comparable_size}")

    # Initialize chunk states + publish jobs (idempotent via meta flag).
    state.ensure_jobs_group(cfg.run_id)
    if state.get_meta(cfg.run_id, "jobs_published") != "1":
        for c in manifest.chunks:
            state.set_chunk(cfg.run_id, c.chunk_id, {
                "status": ChunkStatus.PENDING.value,
                "chunk_index": c.chunk_index,
                "start": c.start,
                "end": c.end,
            })
            state.publish_job(cfg.run_id, {
                "chunk_id": c.chunk_id,
                "chunk_index": c.chunk_index,
                "start": c.start,
                "end": c.end,
            })
        state.set_meta(cfg.run_id, "jobs_published", "1")
        log_event(log, logging.INFO, "jobs.published", f"jobs={len(manifest.chunks)}")
    else:
        log_event(log, logging.INFO, "jobs.skip", "Jobs already published")

    return manifest
