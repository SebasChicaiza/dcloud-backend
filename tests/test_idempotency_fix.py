"""Test that verifies the idempotency fix for already-DONE chunks."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from dna_node.models import ChunkStatus
from dna_node.worker import Worker, WorkerMetrics
from dna_node.config import Config
from dna_node.redis_state import RedisState


def test_submit_skips_already_done_chunks():
    """
    Verify that _submit() does NOT re-process chunks that are already DONE.
    
    This tests the fix for the audit finding: 
    "Worker re-processes chunk even if already DONE."
    """
    # Create mocks
    cfg = Mock(spec=Config)
    cfg.run_id = "test-run"
    cfg.node_id = "node-1"
    cfg.local_partials_dir = "/tmp/partials"
    cfg.worker_concurrency = 2
    
    state = Mock(spec=RedisState)
    scp = Mock()
    metrics = WorkerMetrics()
    
    worker = Worker(cfg, state, scp, metrics)
    in_flight = {}
    
    # Set up: chunk already exists with status DONE
    chunk_id = "chunk_000"
    run_id = "test-run"
    msg_id = "msg-123"
    chunk = {
        "chunk_id": chunk_id,
        "chunk_index": 0,
        "start": 0,
        "end": 100,
    }
    
    # Mock: get_chunk returns DONE status
    state.get_chunk.return_value = {
        "status": ChunkStatus.DONE.value,
        "checksum": "abc123",
        "completed_at": "2024-05-11T12:00:00Z",
    }
    
    # Call _submit
    worker._submit(in_flight, msg_id, chunk)
    
    # Verify:
    # 1. get_chunk was called to check status
    state.get_chunk.assert_called_once_with(run_id, chunk_id)
    
    # 2. set_chunk was NOT called (no re-processing)
    state.set_chunk.assert_not_called()
    
    # 3. ack_job WAS called (remove from queue)
    state.ack_job.assert_called_once_with(run_id, msg_id)
    
    # 4. No futures added to in_flight (no processing)
    assert len(in_flight) == 0
    
    print("✅ Fix validated: already-DONE chunks are skipped")


def test_submit_processes_pending_chunks():
    """
    Verify that _submit() DOES process chunks with PENDING or other status.
    """
    from unittest.mock import MagicMock
    
    cfg = Mock(spec=Config)
    cfg.run_id = "test-run"
    cfg.node_id = "node-1"
    cfg.local_partials_dir = "/tmp/partials"
    cfg.local_input_a = "/tmp/a.bin"
    cfg.local_input_b = "/tmp/b.bin"
    cfg.worker_concurrency = 2
    
    state = Mock(spec=RedisState)
    scp = Mock()
    metrics = WorkerMetrics()
    
    worker = Worker(cfg, state, scp, metrics)
    # ✅ Mock the thread pool executor
    worker._pool = MagicMock()
    worker._pool.submit.return_value = MagicMock()  # Mock future
    
    in_flight = {}
    
    chunk_id = "chunk_000"
    msg_id = "msg-456"
    chunk = {
        "chunk_id": chunk_id,
        "chunk_index": 0,
        "start": 0,
        "end": 100,
    }
    
    # Mock: get_chunk returns PENDING (no status set yet, or explicitly PENDING)
    state.get_chunk.return_value = {
        "status": ChunkStatus.PENDING.value,
    }
    
    worker._submit(in_flight, msg_id, chunk)
    
    # Verify:
    # 1. set_chunk WAS called to update status to PROCESSING
    state.set_chunk.assert_called_once()
    call_args = state.set_chunk.call_args
    assert call_args[0][2]["status"] == ChunkStatus.PROCESSING.value
    
    # 2. Pool.submit was called (job submitted)
    worker._pool.submit.assert_called_once()
    
    print("✅ Fix validated: PENDING chunks are processed normally")
