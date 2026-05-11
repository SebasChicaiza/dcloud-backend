"""Leader loop: manifest, reclaim, commands, reduce."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Iterable

from .commands import apply_command
from .config import Config
from .logging_config import log_event
from .manifest import ensure_manifest_and_jobs
from .models import ChunkStatus, Manifest, RunStatus, WorkerStatus
from .redis_state import RedisState, k_node
from .scp_client import ScpClient

log = logging.getLogger(__name__)


class Leader:
    def __init__(self, cfg: Config, state: RedisState, scp: ScpClient, is_leader_fn):
        self.cfg = cfg
        self.state = state
        self.scp = scp
        self._is_leader = is_leader_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cmd_last_id = "$"  # only read new commands

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="leader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)

    def _run(self) -> None:
        # Wait until we are actually leader
        while not self._stop.is_set() and not self._is_leader():
            self._stop.wait(0.5)

        if self._stop.is_set():
            return

        log_event(log, logging.INFO, "leader.start", "Assuming leader duties")
        self.state.set_run_status(self.cfg.run_id, RunStatus.PREPARING.value)
        manifest = self._ensure_run_assets()
        self.state.set_run_status(self.cfg.run_id, RunStatus.RUNNING.value)

        while not self._stop.is_set() and self._is_leader():
            try:
                self._tick(manifest)
            except Exception:
                log.exception("Leader tick failed")
            # End condition
            if self.state.all_chunks_done(self.cfg.run_id, len(manifest.chunks)):
                self._finalize(manifest)
                break
            if self.state.get_run_status(self.cfg.run_id) == RunStatus.CANCELLED.value:
                log_event(log, logging.WARNING, "leader.cancelled", "Run cancelled")
                break
            self._stop.wait(2.0)

    # ---- setup ----
    def _ensure_run_assets(self) -> Manifest:
        # Make remote dirs exist before any worker tries to upload.
        self.scp.ensure_remote_dir(self.cfg.remote_partials_dir)
        self.scp.ensure_remote_dir(self.cfg.remote_partials_meta_dir)
        self.scp.ensure_remote_dir(self.cfg.remote_final_dir)
        return ensure_manifest_and_jobs(self.cfg, self.state, self.scp)

    # ---- per-tick duties ----
    def _tick(self, manifest: Manifest) -> None:
        self._reap_dead_nodes()
        self._reclaim_stale_jobs()
        self._process_commands()
        if self.state.get_meta(self.cfg.run_id, "rebuild_final") == "1":
            self.state.set_meta(self.cfg.run_id, "rebuild_final", "0")
            if self.state.all_chunks_done(self.cfg.run_id, len(manifest.chunks)):
                self._finalize(manifest)

    def _reap_dead_nodes(self) -> None:
        now = time.time()
        for node_id in self.state.list_active_nodes():
            data = self.state.get_node(node_id)
            try:
                last = float(data.get("last_seen", 0))
            except ValueError:
                last = 0
            if now - last > self.cfg.node_dead_after_seconds:
                log_event(log, logging.WARNING, "node.dead",
                          f"Node {node_id} silent for {now - last:.1f}s")
                self.state.mark_node_dead(node_id)

    def _reclaim_stale_jobs(self) -> None:
        # XAUTOCLAIM stale jobs to a special "reaper" consumer so they re-enter
        # the pending pool for the workers; the workers will pick them via XREADGROUP "0"
        # NOTE: simpler approach for the demo — we let XAUTOCLAIM transfer to a
        # synthetic consumer, then the next XREADGROUP call from a live worker
        # using the same group will re-deliver them once they expire pending.
        claimed = self.state.autoclaim_stale(
            self.cfg.run_id, "leader-reclaim", self.cfg.job_min_idle_ms,
        )
        if claimed:
            log_event(log, logging.WARNING, "jobs.reclaimed",
                      f"reclaimed={len(claimed)} stale jobs")
            # Re-publish their payloads so any live worker can pick them up
            # via the normal ">" cursor, then ACK the old (stuck) messages.
            for msg_id, fields in claimed:
                try:
                    chunk = json.loads(fields.get("chunk", "{}"))
                    if chunk:
                        self.state.publish_job(self.cfg.run_id, {**chunk, "reclaimed": True})
                    self.state.ack_job(self.cfg.run_id, msg_id)
                except Exception:
                    log.exception("Failed to re-publish reclaimed job %s", msg_id)

    def _process_commands(self) -> None:
        msgs = self.state.read_commands(self.cfg.run_id, self._cmd_last_id, block_ms=100)
        for msg_id, fields in msgs:
            self._cmd_last_id = msg_id
            try:
                cmd = json.loads(fields["cmd"])
                apply_command(self.cfg, self.state, cmd)
            except Exception:
                log.exception("Failed to apply command %s", msg_id)

    # ---- reduce: build final file ----
    def _finalize(self, manifest: Manifest) -> None:
        self.state.set_run_status(self.cfg.run_id, RunStatus.REDUCING.value)
        log_event(log, logging.INFO, "reduce.start",
                  f"Concatenating {len(manifest.chunks)} partials")

        # 1) Validate all partials exist & checksums match.
        validated = self._validate_partials(manifest)
        if not validated:
            log_event(log, logging.ERROR, "reduce.invalid",
                      "Validation failed; staying in REDUCING for retry")
            self.state.set_run_status(self.cfg.run_id, RunStatus.RUNNING.value)
            return

        # 2) Concatenate by chunk_index — never by arrival order.
        final_path = f"{self.cfg.remote_final_dir}/similarity_map.out"
        self._concat_remote_partials(manifest, final_path)

        # 3) Summary
        stats = self.state.get_stats(self.cfg.run_id)
        matches = int(stats.get("matches", 0))
        mismatches = int(stats.get("mismatches", 0))
        total = int(stats.get("total_bases", 0))
        pct = (matches / total * 100.0) if total else 0.0
        summary = {
            "run_id": self.cfg.run_id,
            "total_bases": total,
            "matches": matches,
            "mismatches": mismatches,
            "similarity_percentage": round(pct, 6),
            "output_file": final_path,
            "chunks": len(manifest.chunks),
            "finished_at": time.time(),
        }
        # Write summary on the control plane.
        summary_local = Path(self.cfg.local_run_dir) / "summary.json"
        summary_local.parent.mkdir(parents=True, exist_ok=True)
        summary_local.write_text(json.dumps(summary, indent=2))
        self.scp.upload(str(summary_local), f"{self.cfg.remote_run_dir}/summary.json")

        self.state.set_run_status(self.cfg.run_id, RunStatus.COMPLETED.value)
        log_event(log, logging.INFO, "reduce.done",
                  f"similarity={pct:.4f}% final={final_path}")

    def _validate_partials(self, manifest: Manifest) -> bool:
        """Stream each remote partial through SHA256 and compare to expected."""
        for c in manifest.chunks:
            info = self.state.get_chunk(self.cfg.run_id, c.chunk_id)
            expected = info.get("checksum")
            if not expected:
                log_event(log, logging.ERROR, "validate.no_checksum",
                          chunk_id=c.chunk_id)
                return False
            remote = f"{self.cfg.remote_partials_dir}/{c.chunk_id}.out"
            if not self.scp.remote_exists(remote):
                log_event(log, logging.ERROR, "validate.missing",
                          f"Missing partial {remote}", chunk_id=c.chunk_id)
                return False
            # In local-mode we can read the file directly; in real SCP we'd need
            # to fetch it. For a demo, we download to a temp path and hash.
            local = Path(self.cfg.local_run_dir) / "verify" / f"{c.chunk_id}.out"
            local.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.scp.download(remote, str(local))
            except Exception:
                log_event(log, logging.ERROR, "validate.download_failed",
                          chunk_id=c.chunk_id)
                return False
            actual = _file_sha256(str(local))
            if actual != expected:
                log_event(log, logging.ERROR, "validate.checksum_mismatch",
                          f"chunk={c.chunk_id} expected={expected} got={actual}",
                          chunk_id=c.chunk_id)
                # Mark for retry; leader will re-publish via RETRY_CHUNK semantics.
                self.state.set_chunk(self.cfg.run_id, c.chunk_id,
                                     {"status": ChunkStatus.RETRY.value})
                self.state.r.srem(f"runs:{self.cfg.run_id}:chunks:done", c.chunk_id)
                self.state.publish_job(self.cfg.run_id, {
                    "chunk_id": c.chunk_id, "chunk_index": c.chunk_index,
                    "start": c.start, "end": c.end, "retry": True,
                })
                return False
        return True

    def _concat_remote_partials(self, manifest: Manifest, final_remote: str) -> None:
        """Concatenate partials by chunk_index. Writes locally then SCPs final."""
        local_final = Path(self.cfg.local_run_dir) / "final" / "similarity_map.out"
        local_final.parent.mkdir(parents=True, exist_ok=True)
        # Sort by chunk_index — never by arrival order.
        sorted_chunks = sorted(manifest.chunks, key=lambda c: c.chunk_index)
        with open(local_final, "wb") as out:
            for c in sorted_chunks:
                # We already downloaded these for verification — reuse if present.
                src = Path(self.cfg.local_run_dir) / "verify" / f"{c.chunk_id}.out"
                if not src.exists():
                    self.scp.download(
                        f"{self.cfg.remote_partials_dir}/{c.chunk_id}.out",
                        str(src),
                    )
                with open(src, "rb") as f:
                    while True:
                        buf = f.read(8 * 1024 * 1024)
                        if not buf:
                            break
                        out.write(buf)
        self.scp.upload(str(local_final), final_remote)


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(8 * 1024 * 1024)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()
