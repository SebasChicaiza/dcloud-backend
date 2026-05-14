"""Worker loop: consume jobs, compute chunks, upload partials by SCP, ACK."""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path

from .config import Config
from .logging_config import log_event
from .models import ChunkResult, ChunkStatus, WorkerStatus
from .processor import compare_chunk
from .redis_state import RedisState, k_node
from .scp_client import ScpClient, ScpError

log = logging.getLogger(__name__)


class WorkerMetrics:
    def __init__(self):
        self.current_jobs = 0
        self.completed_jobs = 0
        self.failed_jobs = 0
        self.processed_bases = 0
        self._lock = threading.Lock()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(
                current_jobs=self.current_jobs,
                completed_jobs=self.completed_jobs,
                failed_jobs=self.failed_jobs,
                processed_bases=self.processed_bases,
            )


class Worker:
    def __init__(self, cfg: Config, state: RedisState, scp: ScpClient, metrics: WorkerMetrics):
        self.cfg = cfg
        self.state = state
        self.scp = scp
        self.metrics = metrics
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool: ProcessPoolExecutor | None = None
        self._last_reported_status: str | None = None  # Track for event publishing

    # ---- lifecycle ----
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)
        if self._thread:
            self._thread.join(timeout=15)

    # ---- input prep ----
    def _ensure_inputs(self) -> None:
        Path(self.cfg.local_partials_dir).mkdir(parents=True, exist_ok=True)
        for name, local_path in (
            (self.cfg.input_a_name, self.cfg.local_input_a),
            (self.cfg.input_b_name, self.cfg.local_input_b),
        ):
            if local_path.exists() and local_path.stat().st_size > 0:
                continue
            remote = f"{self.cfg.remote_inputs_dir}/{name}"
            log_event(log, logging.INFO, "input.download",
                      f"Fetching {remote} -> {local_path}")
            self.scp.download(remote, str(local_path))

    # ---- control flag ----
    def _control_status(self) -> str:
        v = self.state.r.hget(k_node(self.cfg.node_id), "control_status")
        return v or WorkerStatus.ACTIVE.value

    # ---- main loop ----
    def _run(self) -> None:
        try:
            self._ensure_inputs()
        except Exception:
            log.exception("Failed to ensure inputs; worker exiting")
            return

        self.state.ensure_jobs_group(self.cfg.run_id)
        self._pool = ProcessPoolExecutor(max_workers=self.cfg.worker_concurrency)
        in_flight: dict[Future, tuple[str, dict]] = {}  # future -> (msg_id, chunk)

        log_event(log, logging.INFO, "worker.start",
                  f"concurrency={self.cfg.worker_concurrency}")

        while not self._stop.is_set():
            status = self._control_status()
            
            # Publish event on status change
            if status != self._last_reported_status:
                self._last_reported_status = status
                severity_map = {
                    WorkerStatus.ACTIVE.value: "info",
                    WorkerStatus.PAUSED.value: "warning",
                    WorkerStatus.DRAINING.value: "warning",
                    WorkerStatus.DISABLED.value: "error",
                }
                self.state.publish_event(
                    self.cfg.run_id,
                    event_type="worker_status_changed",
                    severity=severity_map.get(status, "info"),
                    node_id=self.cfg.node_id,
                    message=f"Worker status changed to {status}"
                )
            
            if status == WorkerStatus.DISABLED.value:
                log_event(log, logging.INFO, "worker.disabled", "Sleeping (disabled)")
                self._stop.wait(2.0)
                continue

            # Read more jobs only if there's capacity and we aren't paused/draining.
            capacity = self.cfg.worker_concurrency - len(in_flight)
            accepting_new = status == WorkerStatus.ACTIVE.value
            if accepting_new and capacity > 0:
                self._fetch_and_submit(in_flight, capacity)

            # Reap any completed futures.
            self._reap(in_flight)

            # DRAINING + no in-flight => exit loop.
            if status == WorkerStatus.DRAINING.value and not in_flight:
                log_event(log, logging.INFO, "worker.drained", "All jobs done; exiting")
                break

            if not in_flight:
                self._stop.wait(0.5)

        self._pool.shutdown(wait=True)

    def _fetch_and_submit(self, in_flight: dict, capacity: int) -> None:
        try:
            batches = self.state.read_jobs(
                self.cfg.run_id, self.cfg.node_id,
                count=capacity, block_ms=1000,
            )
        except Exception:
            log.exception("XREADGROUP failed")
            return
        for _stream, msgs in batches:
            for msg_id, fields in msgs:
                try:
                    chunk = json.loads(fields["chunk"])
                except Exception:
                    log.exception("Bad job payload msg_id=%s", msg_id)
                    # ACK to drop the malformed message
                    self.state.ack_job(self.cfg.run_id, msg_id)
                    continue
                self._submit(in_flight, msg_id, chunk)

    def _submit(self, in_flight: dict, msg_id: str, chunk: dict) -> None:
        chunk_id = chunk["chunk_id"]
        
        # ✅ FIX: Check if chunk is already DONE to prevent re-processing duplicates
        existing = self.state.get_chunk(self.cfg.run_id, chunk_id)
        if existing.get("status") == ChunkStatus.DONE.value:
            log_event(log, logging.WARNING, "chunk.already_done",
                      f"Chunk already completed; skipping. chunk={chunk_id}",
                      chunk_id=chunk_id)
            # Still ACK to remove from queue, but don't re-process
            self.state.ack_job(self.cfg.run_id, msg_id)
            return
        
        self.state.set_chunk(self.cfg.run_id, chunk_id, {
            "status": ChunkStatus.PROCESSING.value,
            "worker": self.cfg.node_id,
            "started_at": time.time(),
        })
        with self.metrics._lock:
            self.metrics.current_jobs += 1
        out_path = str(Path(self.cfg.local_partials_dir) / f"{chunk_id}.out")
        fut = self._pool.submit(
            compare_chunk,
            str(self.cfg.local_input_a),
            str(self.cfg.local_input_b),
            int(chunk["start"]),
            int(chunk["end"]),
            out_path,
            chunk_id,
            int(chunk["chunk_index"]),
        )
        in_flight[fut] = (msg_id, chunk)

    def _reap(self, in_flight: dict) -> None:
        done = [f for f in list(in_flight.keys()) if f.done()]
        for fut in done:
            msg_id, chunk = in_flight.pop(fut)
            with self.metrics._lock:
                self.metrics.current_jobs -= 1
            try:
                result: ChunkResult = fut.result()
            except Exception as e:
                log_event(log, logging.ERROR, "chunk.failed",
                          f"chunk={chunk['chunk_id']} err={e}",
                          chunk_id=chunk["chunk_id"], error=str(e))
                with self.metrics._lock:
                    self.metrics.failed_jobs += 1
                self.state.set_chunk(self.cfg.run_id, chunk["chunk_id"], {
                    "status": ChunkStatus.FAILED.value, "error": str(e),
                })
                # Publish failure event
                self.state.publish_event(
                    self.cfg.run_id,
                    event_type="chunk_failed",
                    severity="error",
                    node_id=self.cfg.node_id,
                    message=f"Chunk {chunk['chunk_id']} processing failed: {e}",
                    chunk_id=chunk["chunk_id"]
                )
                # No ACK -> will be reclaimed.
                continue
            self._finalize_chunk(msg_id, chunk, result)

    def _finalize_chunk(self, msg_id: str, chunk: dict, result: ChunkResult) -> None:
        chunk_id = chunk["chunk_id"]
        partial_remote = f"{self.cfg.remote_partials_dir}/{chunk_id}.out"
        meta_remote = f"{self.cfg.remote_partials_meta_dir}/{chunk_id}.json"
        meta = {
            "chunk_id": chunk_id,
            "chunk_index": result.chunk_index,
            "start": chunk["start"],
            "end": chunk["end"],
            "matches": result.matches,
            "mismatches": result.mismatches,
            "total_bases": result.total_bases,
            "checksum": result.checksum,
            "worker": self.cfg.node_id,
            "produced_at": time.time(),
        }
        meta_local = str(Path(self.cfg.local_partials_dir) / f"{chunk_id}.json")
        Path(meta_local).write_text(json.dumps(meta))

        # Upload partial + metadata (each uses tmp+rename internally).
        try:
            self.scp.upload(result.output_path, partial_remote)
            self.scp.upload(meta_local, meta_remote)
        except ScpError as e:
            log_event(log, logging.ERROR, "chunk.upload_failed",
                      f"chunk={chunk_id} err={e}",
                      chunk_id=chunk_id, error=str(e))
            self.state.set_chunk(self.cfg.run_id, chunk_id, {
                "status": ChunkStatus.RETRY.value, "error": str(e),
            })
            # NO ACK: stale entry will be reclaimed by leader / another worker.
            return

        # Only NOW: update state and ACK.
        self.state.set_chunk(self.cfg.run_id, chunk_id, {
            "status": ChunkStatus.DONE.value,
            "matches": result.matches,
            "mismatches": result.mismatches,
            "total_bases": result.total_bases,
            "checksum": result.checksum,
            "completed_at": time.time(),
        })
        self.state.mark_chunk_done(self.cfg.run_id, chunk_id)
        self.state.incr_stat(self.cfg.run_id, "matches", result.matches)
        self.state.incr_stat(self.cfg.run_id, "mismatches", result.mismatches)
        self.state.incr_stat(self.cfg.run_id, "total_bases", result.total_bases)
        self.state.incr_stat(self.cfg.run_id, "completed_chunks", 1)
        self.state.ack_job(self.cfg.run_id, msg_id)

        # Publish success event
        self.state.publish_event(
            self.cfg.run_id,
            event_type="chunk_completed",
            severity="success",
            node_id=self.cfg.node_id,
            message=f"Chunk {chunk_id} completed: {result.matches}/{result.total_bases} matches",
            chunk_id=chunk_id,
            matches=result.matches,
            mismatches=result.mismatches
        )

        with self.metrics._lock:
            self.metrics.completed_jobs += 1
            self.metrics.processed_bases += result.total_bases

        log_event(log, logging.INFO, "chunk.done",
                  f"chunk={chunk_id} matches={result.matches}/{result.total_bases} "
                  f"took={result.duration_ms:.1f}ms",
                  chunk_id=chunk_id, chunk_index=result.chunk_index,
                  matches=result.matches, mismatches=result.mismatches,
                  total_bases=result.total_bases,
                  duration_ms=result.duration_ms)
