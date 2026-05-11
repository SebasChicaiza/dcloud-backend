"""Tests for processor.compare_chunk — correctness, edge cases, checksum determinism."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from dna_node.processor import compare_chunk


def _write(tmp: Path, name: str, data: bytes) -> str:
    p = tmp / name
    p.write_bytes(data)
    return str(p)


def test_identical_chunks_all_X(tmp_path: Path):
    a = _write(tmp_path, "a.bin", b"ACGT" * 256)
    b = _write(tmp_path, "b.bin", b"ACGT" * 256)
    out = str(tmp_path / "out.bin")
    res = compare_chunk(a, b, 0, 1024, out, "chunk_000000", 0)
    assert res.matches == 1024
    assert res.mismatches == 0
    assert res.total_bases == 1024
    body = Path(out).read_bytes()
    assert body == b"X" * 1024
    assert res.checksum == hashlib.sha256(body).hexdigest()


def test_completely_different(tmp_path: Path):
    a = _write(tmp_path, "a.bin", b"A" * 100)
    b = _write(tmp_path, "b.bin", b"T" * 100)
    out = str(tmp_path / "o.bin")
    res = compare_chunk(a, b, 0, 100, out, "chunk_000000", 0)
    assert res.matches == 0
    assert res.mismatches == 100
    assert Path(out).read_bytes() == b"." * 100


def test_mixed(tmp_path: Path):
    a = _write(tmp_path, "a.bin", b"AAAACCCC")
    b = _write(tmp_path, "b.bin", b"ATATCCGG")
    #        match positions:    1 0 1 0 1 1 0 0  -> matches=4
    out = str(tmp_path / "o.bin")
    res = compare_chunk(a, b, 0, 8, out, "c", 0)
    assert res.matches == 4
    assert res.mismatches == 4
    assert Path(out).read_bytes() == b"X.X.XX.."


def test_offset_range(tmp_path: Path):
    a = _write(tmp_path, "a.bin", b"AAAABBBBCCCC")
    b = _write(tmp_path, "b.bin", b"XXXXBBBBYYYY")
    out = str(tmp_path / "o.bin")
    res = compare_chunk(a, b, 4, 8, out, "c", 0)  # bytes [4:8) = "BBBB" vs "BBBB"
    assert res.matches == 4
    assert Path(out).read_bytes() == b"XXXX"


def test_short_read_raises(tmp_path: Path):
    a = _write(tmp_path, "a.bin", b"ACGT")
    b = _write(tmp_path, "b.bin", b"ACGT")
    out = str(tmp_path / "o.bin")
    with pytest.raises(IOError):
        compare_chunk(a, b, 0, 100, out, "c", 0)


def test_checksum_is_deterministic(tmp_path: Path):
    a = _write(tmp_path, "a.bin", os.urandom(4096))
    b = _write(tmp_path, "b.bin", os.urandom(4096))
    out1 = str(tmp_path / "o1.bin")
    out2 = str(tmp_path / "o2.bin")
    r1 = compare_chunk(a, b, 0, 4096, out1, "c", 0)
    r2 = compare_chunk(a, b, 0, 4096, out2, "c", 0)
    assert r1.checksum == r2.checksum
