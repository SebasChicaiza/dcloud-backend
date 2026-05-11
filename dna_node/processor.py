"""Chunk processing — vectorized comparison of two byte ranges.

We use NumPy because chunks are typically 32–128 MB. A pure-Python `for i in range`
loop would take seconds per chunk. NumPy performs the equality check and the
"X"/"." mapping as vectorized C ops — typically ~100x faster — and the SHA256
of the result is computed in a single pass over a contiguous buffer.
"""
from __future__ import annotations

import hashlib
import os
import time

import numpy as np

from .models import ChunkResult

_X = ord("X")
_DOT = ord(".")


def _read_range(path: str, start: int, length: int) -> bytes:
    """Read exactly `length` bytes starting at `start` from `path`."""
    with open(path, "rb") as f:
        f.seek(start)
        return f.read(length)


def compare_chunk(
    file_a_path: str,
    file_b_path: str,
    start: int,
    end: int,
    output_path: str,
    chunk_id: str = "",
    chunk_index: int = 0,
) -> ChunkResult:
    """Compare bytes [start:end) of A vs B; write 'X'/'.' partial; return result."""
    t0 = time.monotonic()
    length = end - start
    if length <= 0:
        raise ValueError(f"Empty range: start={start} end={end}")

    buf_a = _read_range(file_a_path, start, length)
    buf_b = _read_range(file_b_path, start, length)
    if len(buf_a) != length or len(buf_b) != length:
        raise IOError(
            f"Short read for chunk {chunk_id}: "
            f"got A={len(buf_a)} B={len(buf_b)} expected={length}"
        )

    a = np.frombuffer(buf_a, dtype=np.uint8)
    b = np.frombuffer(buf_b, dtype=np.uint8)
    mask = a == b
    out = np.where(mask, _X, _DOT).astype(np.uint8)
    matches = int(np.count_nonzero(mask))
    mismatches = length - matches

    out_bytes = out.tobytes()
    checksum = hashlib.sha256(out_bytes).hexdigest()

    tmp_path = output_path + ".writing"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(tmp_path, "wb") as f:
        f.write(out_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, output_path)

    duration_ms = (time.monotonic() - t0) * 1000.0
    return ChunkResult(
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        matches=matches,
        mismatches=mismatches,
        total_bases=length,
        checksum=checksum,
        output_path=output_path,
        duration_ms=duration_ms,
    )
